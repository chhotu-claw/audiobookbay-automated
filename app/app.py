import hashlib
import mimetypes
import os
import re
import ipaddress
import shutil
import sqlite3
import socket
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, url_for

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
AUDIOBOOKSHELF_URL = os.getenv("AUDIOBOOKSHELF_URL", "").rstrip("/")
AUDIOBOOKSHELF_API_TOKEN = os.getenv("AUDIOBOOKSHELF_API_TOKEN", "")
AUDIOBOOKSHELF_LIBRARY_ID = os.getenv("AUDIOBOOKSHELF_LIBRARY_ID", "")
AUDIOBOOKSHELF_IMPORT_DIR = os.getenv("AUDIOBOOKSHELF_IMPORT_DIR", "")
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma")
ZIP_EXTENSIONS = (".zip",)
ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z")
ZIP_CACHE_DIR = os.getenv("ZIP_CACHE_DIR", "data/cache")
SEVEN_ZIP_BINARY = os.getenv("SEVEN_ZIP_BINARY", "7z")

print(f"ABB_HOSTNAME: {ABB_HOSTNAME}")
print(f"PAGE_LIMIT: {PAGE_LIMIT}")
print(f"PORT: {FLASK_PORT}")
print(f"DATABASE_PATH: {DATABASE_PATH}")
print(f"REAL_DEBRID_API_BASE: {REAL_DEBRID_API_BASE}")
print(f"REAL_DEBRID_API_TOKEN configured: {bool(REAL_DEBRID_API_TOKEN)}")
print(f"AUDIOBOOKSHELF_URL configured: {bool(AUDIOBOOKSHELF_URL)}")
print(f"AUDIOBOOKSHELF_API_TOKEN configured: {bool(AUDIOBOOKSHELF_API_TOKEN)}")


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


class AudiobookshelfError(RuntimeError):
    pass


class AudiobookshelfClient:
    def __init__(self, base_url=None, token=None, session=None):
        self.base_url = (base_url if base_url is not None else app.config.get("AUDIOBOOKSHELF_URL", AUDIOBOOKSHELF_URL)).rstrip("/")
        self.token = token if token is not None else app.config.get("AUDIOBOOKSHELF_API_TOKEN", AUDIOBOOKSHELF_API_TOKEN)
        self.session = session or requests.Session()
        if not self.base_url or not self.token:
            raise AudiobookshelfError("Audiobookshelf is not configured")

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.request(method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise AudiobookshelfError("Audiobookshelf request failed") from exc
        if response.status_code in {401, 403}:
            raise AudiobookshelfError("Audiobookshelf authorization failed")
        if response.status_code >= 400:
            raise AudiobookshelfError(f"Audiobookshelf API error {response.status_code}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def healthcheck(self):
        return self._request("GET", "/healthcheck")

    def library(self, library_id):
        return self._request("GET", f"/api/libraries/{library_id}")

    def scan_library(self, library_id, force=False):
        return self._request("POST", f"/api/libraries/{library_id}/scan", params={"force": "1" if force else "0"})


def abs_config():
    return {
        "url": app.config.get("AUDIOBOOKSHELF_URL", AUDIOBOOKSHELF_URL).rstrip("/"),
        "token": app.config.get("AUDIOBOOKSHELF_API_TOKEN", AUDIOBOOKSHELF_API_TOKEN),
        "library_id": app.config.get("AUDIOBOOKSHELF_LIBRARY_ID", AUDIOBOOKSHELF_LIBRARY_ID),
        "import_dir": app.config.get("AUDIOBOOKSHELF_IMPORT_DIR", AUDIOBOOKSHELF_IMPORT_DIR),
    }


def abs_is_configured(config=None):
    config = config or abs_config()
    return bool(config["url"] and config["token"] and config["library_id"])


def abs_client():
    factory = app.config.get("ABS_CLIENT_FACTORY")
    if factory:
        return factory()
    return AudiobookshelfClient()


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
    if isinstance(error, AudiobookshelfError):
        return "Audiobookshelf request failed"
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


def guess_audio_mimetype(filename):
    lower = filename.lower()
    if lower.endswith(".m4b"):
        return "audio/mp4"
    if lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".opus"):
        return "audio/ogg"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def is_zip_file(filename):
    return filename.lower().endswith(ZIP_EXTENSIONS)


def archive_extension_from_name(name):
    path = unquote(urlparse(name).path if "://" in name else name).lower()
    for extension in ARCHIVE_EXTENSIONS:
        if path.endswith(extension):
            return extension
    return None


def is_archive_url(url):
    return archive_extension_from_name(url) is not None


def get_zip_cache_dir():
    cache_dir = Path(app.config.get("ZIP_CACHE_DIR", ZIP_CACHE_DIR))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def archive_cache_path(file_id, extension=".zip"):
    extension = extension if extension in ARCHIVE_EXTENSIONS else ".archive"
    return get_zip_cache_dir() / f"rd-file-{int(file_id)}{extension}"


def zip_cache_path(file_id):
    return archive_cache_path(file_id, ".zip")


def safe_streaming_get(url, timeout=60, max_redirects=5):
    current = url
    for _ in range(max_redirects + 1):
        try:
            response = requests.get(current, stream=True, timeout=timeout, allow_redirects=False)
        except requests.exceptions.RequestException as exc:
            raise RealDebridError("Real-Debrid download failed") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location") or response.headers.get("Location")
            if hasattr(response, "close"):
                response.close()
            if not location:
                raise RealDebridError("Real-Debrid download redirected without a Location header")
            current = urljoin(current, location)
            if not is_safe_stream_redirect_url(current):
                raise RealDebridError("Real-Debrid returned an unsafe stream redirect URL")
            continue
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise RealDebridError("Real-Debrid download failed") from exc
        return response
    raise RealDebridError("Real-Debrid download redirected too many times")


def download_rd_file_to_cache(file_row, direct=None, extension=None):
    if not direct:
        if not file_row["rd_link"]:
            raise RealDebridError("No Real-Debrid link available yet; sync the book first.")
        unrestricted = rd_client().unrestrict_link(file_row["rd_link"])
        direct = unrestricted.get("download") or unrestricted.get("link")
    if not direct:
        raise RealDebridError("Real-Debrid did not return a downloadable direct link")
    if not is_safe_stream_redirect_url(direct):
        raise RealDebridError("Real-Debrid returned an unsafe stream URL")

    extension = extension or archive_extension_from_name(direct) or archive_extension_from_name(file_row["filename"]) or ".zip"
    destination = archive_cache_path(file_row["id"], extension)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            response = safe_streaming_get(direct, timeout=60)
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


def is_safe_archive_member(name):
    normalized_name = name.replace("\\", "/")
    parts = PurePosixPath(normalized_name).parts
    return bool(normalized_name) and not PurePosixPath(normalized_name).is_absolute() and ".." not in parts


def ensure_zip_cached(file_row):
    path = zip_cache_path(file_row["id"])
    if not path.exists() or path.stat().st_size == 0:
        path = download_rd_file_to_cache(file_row, extension=".zip")
    if not zipfile.is_zipfile(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RealDebridError("Downloaded Real-Debrid file is not a valid ZIP archive")
    return path


def ensure_archive_cached(file_row, direct=None, extension=None):
    extension = extension or (archive_extension_from_name(direct) if direct else None) or archive_extension_from_name(file_row["filename"]) or ".zip"
    path = archive_cache_path(file_row["id"], extension)
    if not path.exists() or path.stat().st_size == 0:
        path = download_rd_file_to_cache(file_row, direct=direct, extension=extension)
    if zipfile.is_zipfile(path):
        return path
    # Validate non-ZIP archives through 7z so bad downloads fail before playback.
    seven_zip_audio_infos(path)
    return path


def safe_zip_audio_infos(zip_path):
    entries = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            normalized_name = info.filename.replace("\\", "/")
            if info.is_dir() or not is_safe_archive_member(normalized_name):
                continue
            if not is_streamable(normalized_name):
                continue
            entries.append({"name": info.filename, "size": info.file_size, "zip_info": info})
    return entries


def parse_7z_slt(output):
    entries = []
    current = {}
    in_listing = False
    for raw_line in output.splitlines():
        line = raw_line.rstrip("\n")
        if line == "----------":
            in_listing = True
            current = {}
            continue
        if not in_listing:
            continue
        if not line:
            if current.get("Path"):
                entries.append(current)
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    if current.get("Path"):
        entries.append(current)
    return entries


def seven_zip_audio_infos(archive_path):
    try:
        result = subprocess.run(
            [SEVEN_ZIP_BINARY, "l", "-slt", str(archive_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RealDebridError("Archive playback requires 7z to be installed on the server") from exc
    if result.returncode != 0:
        raise RealDebridError("Downloaded Real-Debrid file is not a supported archive")

    entries = []
    for entry in parse_7z_slt(result.stdout):
        name = entry.get("Path", "")
        if entry.get("Folder") == "+" or not is_safe_archive_member(name) or not is_streamable(name):
            continue
        try:
            size = int(entry.get("Size") or 0)
        except ValueError:
            size = 0
        entries.append({"name": name, "size": size, "zip_info": None})
    return entries


def natural_sort_key(value):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def archive_audio_infos(archive_path):
    if zipfile.is_zipfile(archive_path):
        entries = safe_zip_audio_infos(archive_path)
    else:
        entries = seven_zip_audio_infos(archive_path)
    return sorted(entries, key=lambda entry: natural_sort_key(entry["name"]))


def resolved_archive_path(file_row):
    if archive_extension_from_name(file_row["filename"]):
        return ensure_archive_cached(file_row)
    if not file_row["rd_link"]:
        return None
    unrestricted = rd_client().unrestrict_link(file_row["rd_link"])
    direct = unrestricted.get("download") or unrestricted.get("link")
    if direct and is_safe_stream_redirect_url(direct) and is_archive_url(direct):
        return ensure_archive_cached(file_row, direct=direct, extension=archive_extension_from_name(direct))
    return None


def archive_playback_items(file_row, archive_path=None):
    archive_path = archive_path or ensure_archive_cached(file_row)
    items = []
    for index, info in enumerate(archive_audio_infos(archive_path)):
        display_name = PurePosixPath(info["name"].replace("\\", "/")).name or info["name"]
        items.append(
            {
                "filename": display_name,
                "bytes": info["size"],
                "streamable": True,
                "stream_url": url_for("stream_zip_entry", file_id=file_row["id"], entry_index=index),
                "note": f"Inside {file_row['filename']}",
            }
        )
    return items


def zip_playback_items(file_row):
    return archive_playback_items(file_row)


def playback_items_for_files(files):
    items = []
    for file_row in files:
        try:
            archive_path = resolved_archive_path(file_row)
        except Exception as e:
            print(f"[WARNING] Archive inspection failed for file {file_row['id']}: {e}")
            archive_path = None

        if archive_path:
            items.extend(archive_playback_items(file_row, archive_path=archive_path))
        elif file_row["streamable"]:
            items.append(
                {
                    "filename": file_row["filename"],
                    "bytes": file_row["bytes"],
                    "streamable": True,
                    "stream_url": url_for("stream", file_id=file_row["id"]),
                    "note": None,
                }
            )
        elif archive_extension_from_name(file_row["filename"]):
            items.append(
                {
                    "filename": file_row["filename"],
                    "bytes": file_row["bytes"],
                    "streamable": False,
                    "stream_url": None,
                    "note": "Archive could not be inspected yet.",
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


def safe_path_component(value, fallback="item"):
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", value or "").strip(" .")
    return cleaned or fallback


def ensure_child_path(parent, child):
    parent_resolved = Path(parent).resolve()
    child_resolved = Path(child).resolve()
    if parent_resolved != child_resolved and parent_resolved not in child_resolved.parents:
        raise ValueError("Unsafe destination path")
    return child_resolved


def atomic_copy_file(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as tmp_file, open(source, "rb") as source_file:
            shutil.copyfileobj(source_file, tmp_file, length=1024 * 1024)
        Path(tmp_name).replace(destination)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def direct_download_url_for_file(file_row):
    if not file_row["rd_link"]:
        raise RealDebridError("No Real-Debrid link available yet; sync the book first.")
    unrestricted = rd_client().unrestrict_link(file_row["rd_link"])
    direct = unrestricted.get("download") or unrestricted.get("link")
    if not direct:
        raise RealDebridError("Real-Debrid did not return a downloadable direct link")
    if not is_safe_stream_redirect_url(direct):
        raise RealDebridError("Real-Debrid returned an unsafe stream URL")
    return direct


def download_direct_audio_to_path(file_row, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    direct = direct_download_url_for_file(file_row)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            response = safe_streaming_get(direct, timeout=60)
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


def import_destination_for_book(book, import_dir):
    root = Path(import_dir)
    title = safe_path_component(book["title"], f"book-{book['id']}")
    target = root / title
    ensure_child_path(root, target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def sequence_destination(target_dir, sequence, name):
    filename = safe_path_component(name, "audio")
    destination = Path(target_dir) / f"{sequence:03d} - {filename}"
    ensure_child_path(target_dir, destination)
    return destination


def import_book_audio_to_directory(book, files, target_dir):
    imported = []
    sequence = 1
    for file_row in files:
        if not archive_extension_from_name(file_row["filename"]) and not file_row["streamable"]:
            continue
        archive_path = resolved_archive_path(file_row)
        if archive_path:
            for entry in archive_audio_infos(archive_path):
                source = extract_archive_entry_to_cache(archive_path, entry)
                display_name = PurePosixPath(entry["name"].replace("\\", "/")).name or entry["name"]
                destination = sequence_destination(target_dir, sequence, display_name)
                atomic_copy_file(source, destination)
                imported.append(destination.name)
                sequence += 1
        elif file_row["streamable"]:
            destination = sequence_destination(target_dir, sequence, file_row["filename"])
            download_direct_audio_to_path(file_row, destination)
            imported.append(destination.name)
            sequence += 1
    return imported


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


def serialize_book(book):
    return {
        "id": book["id"],
        "title": book["title"],
        "cover": book["cover"],
        "language": book["language"],
        "format": book["format"],
        "bitrate": book["bitrate"],
        "file_size": book["file_size"],
        "post_date": book["post_date"],
        "status": book["status"],
        "progress": book["progress"],
        "error": book["error"],
        "library_url": url_for("book_player", book_id=book["id"]),
    }


def serialize_playback_item(item):
    return {
        "filename": item["filename"],
        "bytes": item["bytes"],
        "streamable": item["streamable"],
        "stream_url": item["stream_url"],
        "download_url": item["stream_url"],
        "note": item["note"],
    }


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"query": query, "books": []})
    try:
        return jsonify({"query": query, "books": search_audiobookbay(query)})
    except Exception as e:
        print(f"[WARNING] API search failed: {e}")
        return jsonify({"message": "Search failed"}), 502


@app.route("/api/library")
def api_library():
    auto_sync_books()
    books = get_db().execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()
    return jsonify({"books": [serialize_book(book) for book in books]})


@app.route("/api/books/<int:book_id>")
def api_book(book_id):
    book = get_book(book_id)
    if not book:
        abort(404)
    maybe_auto_sync_book(book_id)
    book = get_book(book_id)
    files = get_db().execute("SELECT * FROM files WHERE book_id = ? ORDER BY rd_link_index", (book_id,)).fetchall()
    playback_items = playback_items_for_files(files)
    return jsonify({"book": serialize_book(book), "files": [serialize_playback_item(item) for item in playback_items]})


@app.route("/api/books/<int:book_id>/prepare", methods=["POST"])
def api_prepare_book(book_id):
    if not get_book(book_id):
        abort(404)
    try:
        sync_book(book_id)
        book = get_book(book_id)
        return jsonify({"message": "Prepared", "book": serialize_book(book)})
    except Exception as e:
        print(f"[WARNING] API prepare failed for book {book_id}: {e}")
        return jsonify({"message": "Prepare failed"}), 502


@app.route("/api/audiobookshelf/status")
def api_audiobookshelf_status():
    config = abs_config()
    base = {
        "configured": abs_is_configured(config),
        "reachable": False,
        "authorized": False,
        "library_found": False,
        "import_dir_configured": bool(config["import_dir"]),
    }
    if not base["configured"]:
        return jsonify(base)
    base["url"] = config["url"]
    base["library_id"] = config["library_id"]
    try:
        client = abs_client()
        client.healthcheck()
        library_payload = client.library(config["library_id"])
        base.update(
            {
                "reachable": True,
                "authorized": True,
                "library_found": True,
                "library": {
                    "id": library_payload.get("id"),
                    "name": library_payload.get("name"),
                    "mediaType": library_payload.get("mediaType"),
                },
            }
        )
    except Exception as e:
        print(f"[WARNING] Audiobookshelf status failed: {e}")
        base["message"] = sanitized_error(e)
    return jsonify(base)


@app.route("/api/audiobookshelf/scan", methods=["POST"])
def api_audiobookshelf_scan():
    config = abs_config()
    if not abs_is_configured(config):
        return jsonify({"message": "Audiobookshelf is not configured"}), 400
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    try:
        abs_client().scan_library(config["library_id"], force=force)
        return jsonify({"message": "Audiobookshelf scan started", "library_id": config["library_id"]})
    except Exception as e:
        print(f"[WARNING] Audiobookshelf scan failed: {e}")
        return jsonify({"message": sanitized_error(e)}), 502


@app.route("/api/books/<int:book_id>/import-to-audiobookshelf", methods=["POST"])
def api_import_book_to_audiobookshelf(book_id):
    config = abs_config()
    if not abs_is_configured(config) or not config["import_dir"]:
        return jsonify({"message": "Audiobookshelf import is not configured"}), 400

    book = get_book(book_id)
    if not book:
        abort(404)

    maybe_auto_sync_book(book_id)
    book = get_book(book_id)
    if book["status"] != "downloaded" or int(book["progress"] or 0) < 100:
        return jsonify({"message": "Book is not downloaded yet"}), 409

    files = get_db().execute(
        """
        SELECT files.*, books.title AS book_title
        FROM files
        JOIN books ON books.id = files.book_id
        WHERE files.book_id = ? AND files.selected = 1
        ORDER BY files.rd_link_index
        """,
        (book_id,),
    ).fetchall()

    try:
        target_dir = import_destination_for_book(book, config["import_dir"])
        imported_files = import_book_audio_to_directory(book, files, target_dir)
        if not imported_files:
            return jsonify({"message": "No playable audio files to import"}), 409
        abs_client().scan_library(config["library_id"], force=True)
        return jsonify(
            {
                "message": "Imported to Audiobookshelf",
                "book_id": book_id,
                "library_id": config["library_id"],
                "count": len(imported_files),
                "imported_files": imported_files,
            }
        )
    except Exception as e:
        print(f"[WARNING] Audiobookshelf import failed for book {book_id}")
        if isinstance(e, (RealDebridError, AudiobookshelfError)):
            message = sanitized_error(e)
        else:
            message = "Import failed"
        return jsonify({"message": message}), 502


@app.route("/library")
def library():
    auto_sync_books()
    books = get_db().execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()
    return render_template("library.html", books=books)


@app.route("/library/<int:book_id>/import-to-audiobookshelf", methods=["POST"])
def import_book_to_audiobookshelf_route(book_id):
    config = abs_config()
    if not abs_is_configured(config) or not config["import_dir"]:
        return redirect(url_for("book_player", book_id=book_id, import_error="Audiobookshelf import is not configured"))

    book = get_book(book_id)
    if not book:
        abort(404)

    maybe_auto_sync_book(book_id)
    book = get_book(book_id)
    if book["status"] != "downloaded" or int(book["progress"] or 0) < 100:
        return redirect(url_for("book_player", book_id=book_id, import_error="Book is not downloaded yet"))

    files = get_db().execute(
        """
        SELECT files.*, books.title AS book_title
        FROM files
        JOIN books ON books.id = files.book_id
        WHERE files.book_id = ? AND files.selected = 1
        ORDER BY files.rd_link_index
        """,
        (book_id,),
    ).fetchall()

    try:
        target_dir = import_destination_for_book(book, config["import_dir"])
        imported_files = import_book_audio_to_directory(book, files, target_dir)
        if not imported_files:
            return redirect(url_for("book_player", book_id=book_id, import_error="No playable audio files to import"))
        abs_client().scan_library(config["library_id"], force=True)
        return redirect(url_for("book_player", book_id=book_id, imported=len(imported_files)))
    except Exception as e:
        print(f"[WARNING] Audiobookshelf web import failed for book {book_id}")
        if isinstance(e, (RealDebridError, AudiobookshelfError)):
            message = sanitized_error(e)
        else:
            message = "Import failed"
        return redirect(url_for("book_player", book_id=book_id, import_error=message))


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
        if is_archive_url(direct):
            archive_path = ensure_archive_cached(file_row, direct=direct, extension=archive_extension_from_name(direct))
            entries = archive_audio_infos(archive_path)
            if not entries:
                raise RealDebridError("Archive did not contain playable audio files")
            return stream_archive_entry_response(archive_path, entries[0])
        return redirect(direct, code=302)
    except Exception as e:
        print(f"[WARNING] Stream failed for file {file_id}: {e}")
        abort(502, sanitized_error(e))


def extracted_archive_dir(archive_path):
    digest = hashlib.sha256(str(archive_path).encode("utf-8")).hexdigest()[:16]
    directory = get_zip_cache_dir() / "extracted" / f"{archive_path.stem}-{digest}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def extracted_archive_entry_path(archive_path, entry):
    name = entry["name"]
    display_name = PurePosixPath(name.replace("\\", "/")).name or "audio"
    safe_display_name = re.sub(r"[^A-Za-z0-9._ -]", "_", display_name).strip(" .") or "audio"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return extracted_archive_dir(archive_path) / f"{digest}-{safe_display_name}"


def extract_archive_entry_to_cache(archive_path, entry):
    target = extracted_archive_entry_path(archive_path, entry)
    expected_size = int(entry.get("size") or 0)
    if target.exists() and target.stat().st_size > 0 and (not expected_size or target.stat().st_size == expected_size):
        return target

    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            if zipfile.is_zipfile(archive_path) and entry.get("zip_info") is not None:
                with zipfile.ZipFile(archive_path) as archive, archive.open(entry["zip_info"]) as source:
                    shutil.copyfileobj(source, tmp_file, length=1024 * 1024)
            else:
                try:
                    result = subprocess.run(
                        [SEVEN_ZIP_BINARY, "x", "-so", str(archive_path), entry["name"]],
                        stdout=tmp_file,
                        stderr=subprocess.PIPE,
                        timeout=900,
                        check=False,
                    )
                except FileNotFoundError as exc:
                    raise RealDebridError("Archive playback requires 7z to be installed on the server") from exc
                if result.returncode != 0:
                    stderr = result.stderr.decode(errors="ignore")[:500]
                    print(f"[WARNING] 7z extraction failed for {archive_path}: {stderr}")
                    raise RealDebridError("Archive audio extraction failed")
        Path(tmp_name).replace(target)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return target


def stream_archive_entry_response(archive_path, entry):
    name = entry["name"]
    mimetype = guess_audio_mimetype(name)
    display_name = PurePosixPath(name.replace("\\", "/")).name or "audio"
    extracted_path = extract_archive_entry_to_cache(archive_path, entry)
    return send_file(
        extracted_path,
        mimetype=mimetype,
        conditional=True,
        as_attachment=False,
        download_name=display_name,
        max_age=0,
    )


@app.route("/stream-zip/<int:file_id>/<int:entry_index>")
def stream_zip_entry(file_id, entry_index):
    file_row = get_db().execute("SELECT files.*, books.title AS book_title FROM files JOIN books ON books.id = files.book_id WHERE files.id = ?", (file_id,)).fetchone()
    if not file_row:
        abort(404)
    try:
        archive_path = resolved_archive_path(file_row)
        if not archive_path:
            abort(404)
        entries = archive_audio_infos(archive_path)
        if entry_index < 0 or entry_index >= len(entries):
            abort(404)
        return stream_archive_entry_response(archive_path, entries[entry_index])
    except Exception as e:
        if getattr(e, "code", None) == 404:
            raise
        print(f"[WARNING] Archive stream failed for file {file_id} entry {entry_index}: {e}")
        abort(502, sanitized_error(e))


@app.route("/status")
def status():
    return redirect(url_for("library"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT)
