import mimetypes
import os
import re
import ipaddress
import sqlite3
import socket
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)

ABB_HOSTNAME = os.getenv("ABB_HOSTNAME", "audiobookbay.lu")
ABB_ALLOWED_HOSTNAMES = {
    host.strip().lower()
    for host in os.getenv("ABB_ALLOWED_HOSTNAMES", "").split(",")
    if host.strip()
}
ABB_ALLOWED_HOSTNAMES.update(
    {
        ABB_HOSTNAME.lower(),
        "audiobookbay.lu",
        "audiobookbay.se",
        "audiobookbay.is",
        "audiobookbay.fi",
        "theaudiobookbay.se",
    }
)
PAGE_LIMIT = int(os.getenv("PAGE_LIMIT", 5))
AUTO_SYNC_LIMIT = int(os.getenv("AUTO_SYNC_LIMIT", 20))
FLASK_PORT = int(os.getenv("PORT", os.getenv("FLASK_PORT", 5078)))
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/audiobooks.db")
REAL_DEBRID_API_TOKEN = os.getenv("REAL_DEBRID_API_TOKEN", "")
REAL_DEBRID_API_BASE = os.getenv("REAL_DEBRID_API_BASE", "https://api.real-debrid.com/rest/1.0")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")
ZIP_EXTENSIONS = (".zip",)
ZIP_CACHE_DIR = os.getenv("ZIP_CACHE_DIR", "data/cache")

print(f"ABB_HOSTNAME: {ABB_HOSTNAME}")
print(f"PAGE_LIMIT: {PAGE_LIMIT}")
print(f"PORT: {FLASK_PORT}")
print(f"DATABASE_PATH: {DATABASE_PATH}")
print(f"REAL_DEBRID_API_BASE: {REAL_DEBRID_API_BASE}")
print(f"REAL_DEBRID_API_TOKEN configured: {bool(REAL_DEBRID_API_TOKEN)}")


@app.context_processor
def inject_nav_link():
    return {}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db_path():
    return app.config.get("DATABASE_PATH", DATABASE_PATH)


def get_db():
    if "db" not in g:
        db_path = Path(get_db_path())
        if db_path.parent and str(db_path.parent) != ".":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            abb_link TEXT NOT NULL UNIQUE,
            cover TEXT,
            language TEXT,
            format TEXT,
            bitrate TEXT,
            file_size TEXT,
            post_date TEXT,
            magnet TEXT,
            rd_id TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            rd_file_id INTEGER,
            rd_link_index INTEGER,
            rd_link TEXT,
            filename TEXT NOT NULL,
            bytes INTEGER,
            selected INTEGER NOT NULL DEFAULT 1,
            streamable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(book_id, rd_link_index),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        """
    )
    db.commit()


with app.app_context():
    init_db()


class RealDebridError(RuntimeError):
    pass


class RealDebridClient:
    def __init__(self, token=None, base_url=None, session=None):
        self.token = token if token is not None else os.getenv("REAL_DEBRID_API_TOKEN", REAL_DEBRID_API_TOKEN)
        self.base_url = (base_url or os.getenv("REAL_DEBRID_API_BASE", REAL_DEBRID_API_BASE)).rstrip("/")
        self.session = session or requests.Session()
        if not self.token:
            raise RealDebridError("REAL_DEBRID_API_TOKEN is not configured")

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        response = self.session.request(method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RealDebridError(f"Real-Debrid API error {response.status_code}: {detail}")
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def add_magnet(self, magnet):
        return self._request("POST", "/torrents/addMagnet", data={"magnet": magnet})

    def select_all_files(self, torrent_id):
        return self._request("POST", f"/torrents/selectFiles/{torrent_id}", data={"files": "all"})

    def torrent_info(self, torrent_id):
        return self._request("GET", f"/torrents/info/{torrent_id}")

    def unrestrict_link(self, link):
        return self._request("POST", "/unrestrict/link", data={"link": link})


def rd_client():
    factory = app.config.get("RD_CLIENT_FACTORY")
    if factory:
        return factory()
    return RealDebridClient()


def _normalized_hostname(parsed):
    try:
        return (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _is_unsafe_host(hostname, resolve=False):
    host = (hostname or "").rstrip(".").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    if resolve:
        try:
            for result in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(result[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved or ip.is_multicast:
                    return True
        except (OSError, ValueError):
            return False
    return False


def is_safe_http_url(url, allowed_hosts=None, resolve=False):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    hostname = _normalized_hostname(parsed)
    if _is_unsafe_host(hostname, resolve=resolve):
        return False
    if allowed_hosts is not None and hostname not in {h.lower() for h in allowed_hosts}:
        return False
    return True


def is_safe_abb_details_url(url):
    return is_safe_http_url(url, allowed_hosts=ABB_ALLOWED_HOSTNAMES)


def is_safe_stream_redirect_url(url):
    return is_safe_http_url(url, resolve=True)


def sanitized_error(error):
    if isinstance(error, RealDebridError):
        return "Real-Debrid request failed"
    return "Request failed"


def search_audiobookbay(query, max_pages=PAGE_LIMIT):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    results = []
    for page in range(1, max_pages + 1):
        url = f"https://{ABB_HOSTNAME}/page/{page}/?s={quote_plus(query.lower())}"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch page {page}: {e}")
            break
        soup = BeautifulSoup(response.text, "html.parser")
        posts = soup.select(".post")
        if not posts:
            break
        for post in posts:
            try:
                title_element = post.select_one(".postTitle > h2 > a")
                if not title_element:
                    continue
                title = title_element.text.strip()
                href = title_element.get("href", "")
                link = href if href.startswith("http") else f"https://{ABB_HOSTNAME}{href}"
                cover_url = post.select_one("img")["src"] if post.select_one("img") else None
                cover = cover_url or "/static/images/default_cover.jpg"
                post_info = post.select_one(".postInfo")
                post_info_text = post_info.get_text(separator=" ", strip=True) if post_info else ""
                language_match = re.search(r"Language:\s*(.*?)(?:\s*Keywords:|$)", post_info_text, re.DOTALL)
                language = language_match.group(1).strip() if language_match else "N/A"
                details_paragraph = post.select_one(".postContent p[style*='text-align:center']")
                post_date, book_format, bitrate, file_size = "N/A", "N/A", "N/A", "N/A"
                if details_paragraph:
                    details_html = str(details_paragraph)
                    post_date_match = re.search(r"Posted:\s*([^<]+)", details_html)
                    post_date = post_date_match.group(1).strip() if post_date_match else "N/A"
                    format_match = re.search(r"Format:\s*<span[^>]*>([^<]+)</span>", details_html)
                    book_format = format_match.group(1).strip() if format_match else "N/A"
                    bitrate_match = re.search(r"Bitrate:\s*<span[^>]*>([^<]+)</span>", details_html)
                    bitrate = bitrate_match.group(1).strip() if bitrate_match else "N/A"
                    file_size_match = re.search(r"File Size:\s*<span[^>]*>([^<]+)</span>\s*([^<]+)", details_html)
                    if file_size_match:
                        file_size = f"{file_size_match.group(1).strip()} {file_size_match.group(2).strip()}"
                results.append({"title": title, "link": link, "cover": cover, "language": language, "post_date": post_date, "format": book_format, "bitrate": bitrate, "file_size": file_size})
            except Exception as e:
                print(f"[ERROR] Could not process a post: {e}")
    return results


def extract_magnet_link(details_url):
    if not is_safe_abb_details_url(details_url):
        raise ValueError("Invalid AudioBookBay details URL")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(details_url, headers=headers, timeout=15, allow_redirects=False)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        magnet = soup.select_one('a[href^="magnet:"]')
        if magnet:
            return magnet["href"]
        info_hash_row = soup.find("td", string=re.compile(r"Info Hash", re.IGNORECASE))
        if not info_hash_row:
            return None
        info_hash = info_hash_row.find_next_sibling("td").text.strip()
        tracker_rows = soup.find_all("td", string=re.compile(r"udp://|http://", re.IGNORECASE))
        trackers = [row.text.strip() for row in tracker_rows] or ["udp://tracker.openbittorrent.com:80", "udp://opentor.org:2710"]
        trackers_query = "&".join(f"tr={quote(tracker)}" for tracker in trackers)
        return f"magnet:?xt=urn:btih:{info_hash}&{trackers_query}"
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to extract magnet link: {e}")
        return None


def is_streamable(filename):
    return filename.lower().endswith(AUDIO_EXTENSIONS)


def is_zip_file(filename):
    return filename.lower().endswith(ZIP_EXTENSIONS)


def get_zip_cache_dir():
    cache_dir = Path(app.config.get("ZIP_CACHE_DIR", ZIP_CACHE_DIR))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def zip_cache_path(file_id):
    return get_zip_cache_dir() / f"rd-file-{int(file_id)}.zip"


def download_rd_file_to_cache(file_row):
    if not file_row["rd_link"]:
        raise RealDebridError("No Real-Debrid link available yet; sync the book first.")
    unrestricted = rd_client().unrestrict_link(file_row["rd_link"])
    direct = unrestricted.get("download") or unrestricted.get("link")
    if not direct:
        raise RealDebridError("Real-Debrid did not return a downloadable direct link")
    if not is_safe_stream_redirect_url(direct):
        raise RealDebridError("Real-Debrid returned an unsafe stream URL")

    destination = zip_cache_path(file_row["id"])
    fd, tmp_name = tempfile.mkstemp(prefix=f"{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            response = requests.get(direct, stream=True, timeout=60)
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp_file.write(chunk)
        Path(tmp_name).replace(destination)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def ensure_zip_cached(file_row):
    path = zip_cache_path(file_row["id"])
    if not path.exists() or path.stat().st_size == 0:
        path = download_rd_file_to_cache(file_row)
    if not zipfile.is_zipfile(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RealDebridError("Downloaded Real-Debrid file is not a valid ZIP archive")
    return path


def safe_zip_audio_infos(zip_path):
    entries = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            normalized_name = info.filename.replace("\\", "/")
            parts = PurePosixPath(normalized_name).parts
            if info.is_dir() or PurePosixPath(normalized_name).is_absolute() or ".." in parts:
                continue
            if not is_streamable(normalized_name):
                continue
            entries.append(info)
    return entries


def zip_playback_items(file_row):
    zip_path = ensure_zip_cached(file_row)
    items = []
    for index, info in enumerate(safe_zip_audio_infos(zip_path)):
        display_name = PurePosixPath(info.filename.replace("\\", "/")).name or info.filename
        items.append(
            {
                "filename": display_name,
                "bytes": info.file_size,
                "streamable": True,
                "stream_url": url_for("stream_zip_entry", file_id=file_row["id"], entry_index=index),
                "note": f"Inside {file_row['filename']}",
            }
        )
    return items


def playback_items_for_files(files):
    items = []
    for file_row in files:
        if file_row["streamable"]:
            items.append(
                {
                    "filename": file_row["filename"],
                    "bytes": file_row["bytes"],
                    "streamable": True,
                    "stream_url": url_for("stream", file_id=file_row["id"]),
                    "note": None,
                }
            )
        elif is_zip_file(file_row["filename"]):
            try:
                items.extend(zip_playback_items(file_row))
            except Exception as e:
                print(f"[WARNING] ZIP inspection failed for file {file_row['id']}: {e}")
                items.append(
                    {
                        "filename": file_row["filename"],
                        "bytes": file_row["bytes"],
                        "streamable": False,
                        "stream_url": None,
                        "note": "ZIP archive could not be inspected yet.",
                    }
                )
        else:
            items.append(
                {
                    "filename": file_row["filename"],
                    "bytes": file_row["bytes"],
                    "streamable": False,
                    "stream_url": None,
                    "note": None,
                }
            )
    return items


def row_to_dict(row):
    return dict(row) if row else None


def get_book(book_id):
    return get_db().execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def upsert_files(book_id, info):
    links = info.get("links") or []
    files = [f for f in (info.get("files") or []) if f.get("selected", 0)] or info.get("files") or []
    ts = now_iso()
    db = get_db()
    for idx, link in enumerate(links):
        meta = files[idx] if idx < len(files) else {}
        filename = meta.get("path") or meta.get("filename") or f"File {idx + 1}"
        filename = filename.split("/")[-1] or filename
        db.execute(
            """
            INSERT INTO files (book_id, rd_file_id, rd_link_index, rd_link, filename, bytes, selected, streamable, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id, rd_link_index) DO UPDATE SET
                rd_file_id=excluded.rd_file_id, rd_link=excluded.rd_link, filename=excluded.filename,
                bytes=excluded.bytes, selected=excluded.selected, streamable=excluded.streamable, updated_at=excluded.updated_at
            """,
            (book_id, meta.get("id"), idx, link, filename, meta.get("bytes"), 1 if meta.get("selected", 1) else 0, 1 if is_streamable(filename) else 0, ts, ts),
        )
    db.commit()


def sync_book(book_id):
    book = get_book(book_id)
    if not book:
        abort(404)
    if not book["rd_id"]:
        return book
    try:
        info = rd_client().torrent_info(book["rd_id"])
        status = info.get("status") or book["status"]
        progress = int(info.get("progress") or 0)
        ts = now_iso()
        get_db().execute("UPDATE books SET status = ?, progress = ?, error = NULL, updated_at = ? WHERE id = ?", (status, progress, ts, book_id))
        get_db().commit()
        if links := info.get("links"):
            upsert_files(book_id, info)
        return get_book(book_id)
    except Exception as e:
        get_db().execute("UPDATE books SET error = ?, updated_at = ? WHERE id = ?", (sanitized_error(e), now_iso(), book_id))
        get_db().commit()
        raise


def book_needs_auto_sync(book):
    if not book or not book["rd_id"]:
        return False
    if book["status"] != "downloaded":
        return True
    file_count = get_db().execute("SELECT COUNT(*) AS count FROM files WHERE book_id = ?", (book["id"],)).fetchone()["count"]
    return file_count == 0


def maybe_auto_sync_book(book_id):
    book = get_book(book_id)
    if not book_needs_auto_sync(book):
        return
    try:
        sync_book(book_id)
    except Exception as e:
        print(f"[WARNING] Auto-sync failed for book {book_id}: {e}")


def auto_sync_books(limit=AUTO_SYNC_LIMIT):
    candidates = get_db().execute(
        """
        SELECT books.* FROM books
        LEFT JOIN files ON files.book_id = books.id
        WHERE books.rd_id IS NOT NULL
        GROUP BY books.id
        HAVING books.status != 'downloaded' OR COUNT(files.id) = 0
        ORDER BY books.updated_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for book in candidates:
        try:
            sync_book(book["id"])
        except Exception as e:
            print(f"[WARNING] Auto-sync failed for book {book['id']}: {e}")


@app.route("/", methods=["GET", "POST"])
def search():
    books = []
    query = ""
    try:
        if request.method == "POST":
            query = request.form["query"]
            if query:
                books = search_audiobookbay(query)
        return render_template("search.html", books=books, query=query)
    except Exception as e:
        return render_template("search.html", books=books, error=f"Failed to search. {str(e)}", query=query)


@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.get_json(silent=True) or {}
    details_url = data.get("link")
    title = data.get("title")
    if not details_url or not title:
        return jsonify({"message": "Missing title or link"}), 400
    if not is_safe_abb_details_url(details_url):
        return jsonify({"message": "Invalid AudioBookBay details URL"}), 400
    db = get_db()
    existing = db.execute("SELECT * FROM books WHERE abb_link = ?", (details_url,)).fetchone()
    if existing:
        if existing["rd_id"] and existing["status"] == "error":
            try:
                sync_book(existing["id"])
            except Exception as e:
                print(f"[WARNING] Existing errored book sync failed for {existing['id']}: {e}")
        return jsonify({"message": "Already in library", "book_id": existing["id"], "library_url": url_for("book_player", book_id=existing["id"])})
    try:
        magnet = extract_magnet_link(details_url)
    except ValueError:
        return jsonify({"message": "Invalid AudioBookBay details URL"}), 400
    if not magnet:
        return jsonify({"message": "Failed to extract magnet link"}), 500
    ts = now_iso()
    cur = db.execute(
        """
        INSERT INTO books (title, abb_link, cover, language, format, bitrate, file_size, post_date, magnet, status, progress, added_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'adding', 0, ?, ?)
        """,
        (title, details_url, data.get("cover"), data.get("language"), data.get("format"), data.get("bitrate"), data.get("file_size"), data.get("post_date"), magnet, ts, ts),
    )
    book_id = cur.lastrowid
    db.commit()
    try:
        rd = rd_client()
        added = rd.add_magnet(magnet)
        rd_id = added.get("id")
        if not rd_id:
            raise RealDebridError("Real-Debrid did not return a torrent id")
        db.execute("UPDATE books SET rd_id = ?, status = 'queued', updated_at = ? WHERE id = ?", (rd_id, now_iso(), book_id))
        db.commit()
        rd.select_all_files(rd_id)
        try:
            sync_book(book_id)
        except Exception as e:
            print(f"[WARNING] Initial sync failed for book {book_id}: {e}")
        return jsonify({"message": "Added to Real-Debrid", "book_id": book_id, "library_url": url_for("book_player", book_id=book_id)})
    except Exception as e:
        safe_message = sanitized_error(e)
        db.execute("UPDATE books SET status = 'error', error = ?, updated_at = ? WHERE id = ?", (safe_message, now_iso(), book_id))
        db.commit()
        return jsonify({"message": safe_message, "book_id": book_id}), 502


@app.route("/library")
def library():
    auto_sync_books()
    books = get_db().execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()
    return render_template("library.html", books=books)


@app.route("/library/<int:book_id>/sync", methods=["POST"])
def sync_book_route(book_id):
    try:
        sync_book(book_id)
        return redirect(url_for("book_player", book_id=book_id))
    except Exception as e:
        print(f"[WARNING] Manual sync failed for book {book_id}: {e}")
        return jsonify({"message": "Sync failed"}), 502


@app.route("/book/<int:book_id>")
def book_player(book_id):
    book = get_book(book_id)
    if not book:
        abort(404)
    maybe_auto_sync_book(book_id)
    book = get_book(book_id)
    files = get_db().execute("SELECT * FROM files WHERE book_id = ? ORDER BY rd_link_index", (book_id,)).fetchall()
    playback_items = playback_items_for_files(files)
    return render_template("player.html", book=book, files=files, playback_items=playback_items)


@app.route("/stream/<int:file_id>")
def stream(file_id):
    file_row = get_db().execute("SELECT files.*, books.title AS book_title FROM files JOIN books ON books.id = files.book_id WHERE files.id = ?", (file_id,)).fetchone()
    if not file_row:
        abort(404)
    if not file_row["rd_link"]:
        abort(404, "No Real-Debrid link available yet; sync the book first.")
    try:
        unrestricted = rd_client().unrestrict_link(file_row["rd_link"])
        direct = unrestricted.get("download") or unrestricted.get("link")
        if not direct:
            raise RealDebridError("Real-Debrid did not return a playable direct link")
        if not is_safe_stream_redirect_url(direct):
            raise RealDebridError("Real-Debrid returned an unsafe stream URL")
        return redirect(direct, code=302)
    except Exception as e:
        print(f"[WARNING] Stream failed for file {file_id}: {e}")
        abort(502, sanitized_error(e))


@app.route("/stream-zip/<int:file_id>/<int:entry_index>")
def stream_zip_entry(file_id, entry_index):
    file_row = get_db().execute("SELECT files.*, books.title AS book_title FROM files JOIN books ON books.id = files.book_id WHERE files.id = ?", (file_id,)).fetchone()
    if not file_row or not is_zip_file(file_row["filename"]):
        abort(404)
    try:
        zip_path = ensure_zip_cached(file_row)
        entries = safe_zip_audio_infos(zip_path)
        if entry_index < 0 or entry_index >= len(entries):
            abort(404)
        info = entries[entry_index]
        with zipfile.ZipFile(zip_path) as archive:
            data = archive.read(info)
        mimetype = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
        display_name = PurePosixPath(info.filename.replace("\\", "/")).name or "audio"
        response = Response(data, mimetype=mimetype)
        response.headers["Content-Length"] = str(len(data))
        response.headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(display_name)}"
        return response
    except Exception as e:
        if getattr(e, "code", None) == 404:
            raise
        print(f"[WARNING] ZIP stream failed for file {file_id} entry {entry_index}: {e}")
        abort(502, sanitized_error(e))


@app.route("/status")
def status():
    return redirect(url_for("library"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
