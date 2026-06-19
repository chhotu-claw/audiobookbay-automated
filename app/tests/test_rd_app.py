import os
import sys
import zipfile
from io import BytesIO
from pathlib import Path

os.environ.setdefault("DATABASE_PATH", "/tmp/audiobookbay-rd-tests-import.db")
os.environ.setdefault("REAL_DEBRID_API_TOKEN", "test-token")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402


SAFE_ABB_LINK = "https://audiobookbay.lu/test-book"


class FakeRealDebrid:
    def __init__(self):
        self.added_magnets = []
        self.selected = []
        self.info = {
            "id": "rd-1",
            "status": "downloaded",
            "progress": 100,
            "files": [
                {"id": 1, "path": "/Book/Chapter 01.mp3", "bytes": 1234, "selected": 1},
                {"id": 2, "path": "/Book/cover.jpg", "bytes": 99, "selected": 1},
            ],
            "links": ["https://rd.example/link-1", "https://rd.example/link-2"],
        }

    def add_magnet(self, magnet):
        self.added_magnets.append(magnet)
        return {"id": "rd-1"}

    def select_all_files(self, torrent_id):
        self.selected.append(torrent_id)
        return {}

    def torrent_info(self, torrent_id):
        assert torrent_id == "rd-1"
        return self.info

    def unrestrict_link(self, link):
        return {"download": f"https://download.example/{link.rsplit('/', 1)[-1]}"}


class FailingSelectRealDebrid(FakeRealDebrid):
    def select_all_files(self, torrent_id):
        self.selected.append(torrent_id)
        raise app_module.RealDebridError("token=secret provider stack detail")


class UnsafeStreamRealDebrid(FakeRealDebrid):
    def unrestrict_link(self, link):
        return {"download": "http://127.0.0.1/private.mp3"}


class ArchiveStreamRealDebrid(FakeRealDebrid):
    def unrestrict_link(self, link):
        return {"download": "https://download.example/Book.rar"}


class ZipRealDebrid(FakeRealDebrid):
    def unrestrict_link(self, link):
        return {"download": "https://download.example/book.zip"}

class FakeAudiobookshelf:
    def __init__(self):
        self.scans = []
        self.library_payload = {"id": "lib-1", "name": "Audiobooks", "mediaType": "book"}

    def healthcheck(self):
        return {"status": "ok"}

    def library(self, library_id):
        assert library_id == "lib-1"
        return self.library_payload

    def scan_library(self, library_id, force=False):
        self.scans.append((library_id, force))
        return {"started": True}

class FailingAudiobookshelf(FakeAudiobookshelf):
    def library(self, library_id):
        raise app_module.AudiobookshelfError("AUDIOBOOKSHELF_API_TOKEN=secret detail")

    def scan_library(self, library_id, force=False):
        raise app_module.AudiobookshelfError("AUDIOBOOKSHELF_API_TOKEN=secret detail")

class FakeZipDownloadResponse:
    status_code = 200
    headers = {"content-length": "1"}

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024 * 1024):
        yield self.content

class FakeRedirectDownloadResponse:
    status_code = 302
    headers = {"Location": "http://127.0.0.1/private.mp3"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1024 * 1024):
        yield b"should-not-download"

class FakeFailingDownloadResponse:
    status_code = 500
    headers = {}
    url = "https://download.example/file.mp3?token=download-secret"

    def raise_for_status(self):
        raise app_module.requests.exceptions.HTTPError(f"500 Server Error for url: {self.url}")

    def iter_content(self, chunk_size=1024 * 1024):
        yield b"should-not-download"

def reset_db(db_path):
    app_module.app.config.pop("RD_CLIENT_FACTORY", None)
    app_module.app.config.pop("ZIP_CACHE_DIR", None)
    app_module.app.config.pop("ABS_CLIENT_FACTORY", None)
    app_module.app.config["AUDIOBOOKSHELF_URL"] = ""
    app_module.app.config["AUDIOBOOKSHELF_API_TOKEN"] = ""
    app_module.app.config["AUDIOBOOKSHELF_LIBRARY_ID"] = ""
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = ""

    app_module.app.config["DATABASE_PATH"] = str(db_path)
    with app_module.app.app_context():
        db = app_module.get_db()
        db.executescript("DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS books;")
        db.commit()
        app_module.init_db()


def build_test_zip():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("A Lemon Tree/01 Opening.mp3", b"mp3-audio")
        archive.writestr("A Lemon Tree/02 Next.m4b", b"m4b-audio")
        archive.writestr("A Lemon Tree/cover.jpg", b"image")
        archive.writestr("../escape.mp3", b"unsafe")
    return buffer.getvalue()


def test_search_page_uses_mocked_scraper(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    monkeypatch.setattr(
        app_module,
        "search_audiobookbay",
        lambda query: [
            {
                "title": "Test Book",
                "link": SAFE_ABB_LINK,
                "cover": "/static/images/default_cover.jpg",
                "language": "English",
                "post_date": "01 Jan 2024",
                "format": "MP3",
                "bitrate": "128 kbps",
                "file_size": "10 MB",
            }
        ],
    )

    client = app_module.app.test_client()
    response = client.post("/", data={"query": "test"})

    assert response.status_code == 200
    assert b"Test Book" in response.data
    assert b"Add to Real-Debrid" in response.data
    assert b"Details" not in response.data


def test_api_add_persists_book_and_files(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_rd = FakeRealDebrid()
    monkeypatch.setattr(app_module, "extract_magnet_link", lambda url: "magnet:?xt=urn:btih:abc")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: fake_rd

    client = app_module.app.test_client()
    response = client.post(
        "/api/add",
        json={
            "title": "Test Book",
            "link": SAFE_ABB_LINK,
            "cover": "/cover.jpg",
            "language": "English",
            "format": "MP3",
            "bitrate": "128 kbps",
            "file_size": "10 MB",
            "post_date": "01 Jan 2024",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["message"] == "Added to Real-Debrid"
    assert fake_rd.added_magnets == ["magnet:?xt=urn:btih:abc"]
    assert fake_rd.selected == ["rd-1"]

    with app_module.app.app_context():
        book = app_module.get_db().execute("SELECT * FROM books").fetchone()
        files = app_module.get_db().execute("SELECT * FROM files ORDER BY rd_link_index").fetchall()
    assert book["title"] == "Test Book"
    assert book["rd_id"] == "rd-1"
    assert book["status"] == "downloaded"
    assert book["progress"] == 100
    assert len(files) == 2
    assert files[0]["filename"] == "Chapter 01.mp3"
    assert files[0]["streamable"] == 1
    assert files[1]["streamable"] == 0

    app_module.app.config.pop("RD_CLIENT_FACTORY", None)


def test_stream_redirects_to_unrestricted_link(tmp_path):
    reset_db(tmp_path / "test.db")
    fake_rd = FakeRealDebrid()
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: fake_rd
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/link-1", "Chapter.mp3", 1, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    client = app_module.app.test_client()
    response = client.get(f"/stream/{file_id}")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://download.example/link-1"
    app_module.app.config.pop("RD_CLIENT_FACTORY", None)


def test_library_and_player_pages_render(tmp_path):
    reset_db(tmp_path / "test.db")
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "queued", 0, app_module.now_iso(), app_module.now_iso()),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.commit()

    client = app_module.app.test_client()
    library = client.get("/library")
    player = client.get(f"/book/{book_id}")

    assert library.status_code == 200
    assert b"Real-Debrid Library" in library.data
    assert player.status_code == 200
    assert b"No playable files" in player.data


def test_api_add_rejects_invalid_details_url(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    monkeypatch.setattr(app_module, "extract_magnet_link", lambda url: (_ for _ in ()).throw(AssertionError("should not extract")))

    client = app_module.app.test_client()
    response = client.post("/api/add", json={"title": "Bad", "link": "http://127.0.0.1/admin"})

    assert response.status_code == 400
    assert response.get_json()["message"] == "Invalid AudioBookBay details URL"


def test_api_add_missing_token_returns_sanitized_error(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    monkeypatch.setattr(app_module, "extract_magnet_link", lambda url: "magnet:?xt=urn:btih:abc")
    monkeypatch.setenv("REAL_DEBRID_API_TOKEN", "")
    monkeypatch.setattr(app_module, "REAL_DEBRID_API_TOKEN", "")

    client = app_module.app.test_client()
    response = client.post("/api/add", json={"title": "Book", "link": SAFE_ABB_LINK})

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    assert b"REAL_DEBRID_API_TOKEN" not in response.data


def test_partial_add_failure_saves_rd_id_and_sanitizes_error(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_rd = FailingSelectRealDebrid()
    monkeypatch.setattr(app_module, "extract_magnet_link", lambda url: "magnet:?xt=urn:btih:abc")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: fake_rd

    client = app_module.app.test_client()
    response = client.post("/api/add", json={"title": "Book", "link": SAFE_ABB_LINK})

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    with app_module.app.app_context():
        book = app_module.get_db().execute("SELECT * FROM books").fetchone()
    assert book["rd_id"] == "rd-1"
    assert book["status"] == "error"
    assert book["error"] == "Real-Debrid request failed"


def test_stream_rejects_unsafe_redirect_url(tmp_path):
    reset_db(tmp_path / "test.db")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: UnsafeStreamRealDebrid()
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/link-1", "Chapter.mp3", 1, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    client = app_module.app.test_client()
    response = client.get(f"/stream/{file_id}")

    assert response.status_code == 502
    assert "Location" not in response.headers


def test_stream_serves_first_audio_when_real_debrid_returns_archive_for_audio_file(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ArchiveStreamRealDebrid()
    zip_bytes = build_test_zip()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(zip_bytes))
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/link-1", "Chapter.mp3", 1, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    response = app_module.app.test_client().get(f"/stream/{file_id}")

    assert response.status_code == 200
    assert response.data == b"mp3-audio"
    assert response.headers["Content-Type"].startswith("audio/mpeg")
    assert "Location" not in response.headers


def test_player_lists_audio_entries_inside_zip_without_exposing_direct_url(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ZipRealDebrid()
    zip_bytes = build_test_zip()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(zip_bytes))
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("A Lemon Tree", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/zip-link", "A Lemon Tree.zip", 0, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    response = app_module.app.test_client().get(f"/book/{book_id}")

    assert response.status_code == 200
    assert b"01 Opening.mp3" in response.data
    assert b"02 Next.m4b" in response.data
    assert b"A Lemon Tree/cover.jpg" not in response.data
    assert b"escape.mp3" not in response.data
    assert b"https://download.example/book.zip" not in response.data
    assert f"/stream-zip/{file_id}/0".encode() in response.data


def test_stream_zip_entry_serves_audio_from_cached_archive(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ZipRealDebrid()
    zip_bytes = build_test_zip()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(zip_bytes))
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("A Lemon Tree", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/zip-link", "A Lemon Tree.zip", 0, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    response = app_module.app.test_client().get(f"/stream-zip/{file_id}/0")

    assert response.status_code == 200
    assert response.data == b"mp3-audio"
    assert response.headers["Content-Type"].startswith("audio/mpeg")


def test_stream_zip_entry_supports_range_requests_from_cached_audio(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ZipRealDebrid()
    zip_bytes = build_test_zip()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(zip_bytes))
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("A Lemon Tree", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/zip-link", "A Lemon Tree.zip", 0, app_module.now_iso(), app_module.now_iso()),
        )
        file_id = db.execute("SELECT id FROM files").fetchone()["id"]
        db.commit()

    response = app_module.app.test_client().get(f"/stream-zip/{file_id}/0", headers={"Range": "bytes=0-2"})

    assert response.status_code == 206
    assert response.data == b"mp3"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Range"].startswith("bytes 0-2/")



def test_api_book_expands_resolved_archive_link_even_when_db_filename_is_mp3(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ArchiveStreamRealDebrid()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Book-Part05.mp3", b"part-five")
        archive.writestr("Book-Part01.mp3", b"part-one")
        archive.writestr("Book-Part02.mp3", b"part-two")
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(buffer.getvalue()))
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/link-1", "Book-Part05.mp3", 1, app_module.now_iso(), app_module.now_iso()),
        )
        db.commit()

    response = app_module.app.test_client().get(f"/api/books/{book_id}")

    assert response.status_code == 200
    filenames = [item["filename"] for item in response.json["files"]]
    assert filenames == ["Book-Part01.mp3", "Book-Part02.mp3", "Book-Part05.mp3"]
    assert [item["stream_url"] for item in response.json["files"]] == ["/stream-zip/1/0", "/stream-zip/1/1", "/stream-zip/1/2"]


def test_m4b_archive_entries_are_served_as_audio_mp4():
    assert app_module.guess_audio_mimetype("Book.m4b") == "audio/mp4"



def test_api_library_and_book_return_playback_urls(tmp_path):
    reset_db(tmp_path / "test.db")
    fake_rd = FakeRealDebrid()
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: fake_rd
    with app_module.app.app_context():
        db = app_module.get_db()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Book", "https://audiobookbay.lu/book", "downloaded", 100, app_module.now_iso(), app_module.now_iso(), "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, bytes, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, "https://rd.example/link-1", "Chapter.mp3", 1234, 1, app_module.now_iso(), app_module.now_iso()),
        )
        db.commit()

    client = app_module.app.test_client()
    library = client.get("/api/library")
    assert library.status_code == 200
    assert library.json["books"][0]["title"] == "Book"

    detail = client.get(f"/api/books/{book_id}")
    assert detail.status_code == 200
    assert detail.json["book"]["id"] == book_id
    assert detail.json["files"][0]["stream_url"].startswith("/stream/")


def test_api_search_uses_existing_scraper(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    monkeypatch.setattr(app_module, "search_audiobookbay", lambda query: [{"title": "Result", "link": SAFE_ABB_LINK}])

    response = app_module.app.test_client().get("/api/search?q=result")

    assert response.status_code == 200
    assert response.json["query"] == "result"
    assert response.json["books"] == [{"title": "Result", "link": SAFE_ABB_LINK}]

def test_send_alias_removed(tmp_path):
    reset_db(tmp_path / "test.db")
    response = app_module.app.test_client().post("/send", json={"title": "Book", "link": SAFE_ABB_LINK})
    assert response.status_code == 404


def configure_abs(fake=None):
    app_module.app.config["AUDIOBOOKSHELF_URL"] = "http://abs.local"
    app_module.app.config["AUDIOBOOKSHELF_API_TOKEN"] = "abs-secret-token"
    app_module.app.config["AUDIOBOOKSHELF_LIBRARY_ID"] = "lib-1"
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = "/abs-import"
    if fake is not None:
        app_module.app.config["ABS_CLIENT_FACTORY"] = lambda: fake


def test_audiobookshelf_status_unconfigured(tmp_path):
    reset_db(tmp_path / "test.db")

    response = app_module.app.test_client().get("/api/audiobookshelf/status")

    assert response.status_code == 200
    assert response.get_json() == {
        "configured": False,
        "reachable": False,
        "authorized": False,
        "library_found": False,
        "import_dir_configured": False,
    }


def test_audiobookshelf_status_configured_without_leaking_token(tmp_path):
    reset_db(tmp_path / "test.db")
    configure_abs(FakeAudiobookshelf())

    response = app_module.app.test_client().get("/api/audiobookshelf/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["configured"] is True
    assert payload["url"] == "http://abs.local"
    assert payload["library_id"] == "lib-1"
    assert payload["import_dir_configured"] is True
    assert payload["reachable"] is True
    assert payload["authorized"] is True
    assert payload["library_found"] is True
    assert payload["library"] == {"id": "lib-1", "name": "Audiobooks", "mediaType": "book"}
    assert b"abs-secret-token" not in response.data


def test_audiobookshelf_status_failure_is_sanitized(tmp_path):
    reset_db(tmp_path / "test.db")
    configure_abs(FailingAudiobookshelf())

    response = app_module.app.test_client().get("/api/audiobookshelf/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["configured"] is True
    assert payload["reachable"] is False
    assert payload["authorized"] is False
    assert payload["library_found"] is False
    assert payload["message"] == "Audiobookshelf request failed"
    assert b"abs-secret-token" not in response.data
    assert b"AUDIOBOOKSHELF_API_TOKEN" not in response.data


def test_audiobookshelf_scan_requires_configuration(tmp_path):
    reset_db(tmp_path / "test.db")

    response = app_module.app.test_client().post("/api/audiobookshelf/scan", json={"force": True})

    assert response.status_code == 400
    assert response.get_json()["message"] == "Audiobookshelf is not configured"


def test_audiobookshelf_scan_triggers_configured_library(tmp_path):
    reset_db(tmp_path / "test.db")
    fake = FakeAudiobookshelf()
    configure_abs(fake)

    response = app_module.app.test_client().post("/api/audiobookshelf/scan", json={"force": True})

    assert response.status_code == 200
    assert response.get_json() == {"message": "Audiobookshelf scan started", "library_id": "lib-1"}
    assert fake.scans == [("lib-1", True)]


def test_audiobookshelf_scan_failure_is_sanitized(tmp_path):
    reset_db(tmp_path / "test.db")
    configure_abs(FailingAudiobookshelf())

    response = app_module.app.test_client().post("/api/audiobookshelf/scan", json={"force": False})

    assert response.status_code == 502
    assert response.get_json()["message"] == "Audiobookshelf request failed"
    assert b"AUDIOBOOKSHELF_API_TOKEN" not in response.data
    assert b"abs-secret-token" not in response.data


def insert_book_with_file(title="Book", status="downloaded", progress=100, filename="Chapter 01.mp3", rd_link="https://rd.example/link-1", streamable=1):
    with app_module.app.app_context():
        db = app_module.get_db()
        ts = app_module.now_iso()
        db.execute(
            "INSERT INTO books (title, abb_link, status, progress, added_at, updated_at, rd_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, f"https://audiobookbay.lu/{title.replace(' ', '-').lower()}", status, progress, ts, ts, "rd-1"),
        )
        book_id = db.execute("SELECT id FROM books ORDER BY id DESC LIMIT 1").fetchone()["id"]
        db.execute(
            "INSERT INTO files (book_id, rd_link_index, rd_link, filename, streamable, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (book_id, 0, rd_link, filename, streamable, ts, ts),
        )
        db.commit()
    return book_id


def test_audiobookshelf_import_requires_import_configuration(tmp_path):
    reset_db(tmp_path / "test.db")
    fake = FakeAudiobookshelf()
    configure_abs(fake)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = ""
    book_id = insert_book_with_file()

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 400
    assert response.get_json()["message"] == "Audiobookshelf import is not configured"
    assert fake.scans == []


def test_audiobookshelf_import_rejects_incomplete_book(tmp_path):
    reset_db(tmp_path / "test.db")
    fake = FakeAudiobookshelf()
    configure_abs(fake)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    book_id = insert_book_with_file(status="downloading", progress=50)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 409
    assert response.get_json()["message"] == "Book is not downloaded yet"
    assert fake.scans == []
    assert not (tmp_path / "abs-import").exists()


def test_audiobookshelf_import_direct_streamable_file_and_triggers_scan(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: FakeRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(b"mp3-audio"))
    book_id = insert_book_with_file(title="A Lemon Tree", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["message"] == "Imported to Audiobookshelf"
    assert payload["count"] == 1
    assert payload["imported_files"] == ["001 - Chapter 01.mp3"]
    assert (tmp_path / "abs-import" / "A Lemon Tree" / "001 - Chapter 01.mp3").read_bytes() == b"mp3-audio"
    assert fake_abs.scans == [("lib-1", True)]
    assert b"abs-secret-token" not in response.data
    assert b"download.example" not in response.data


def test_audiobookshelf_import_zip_archive_extracts_safe_audio_only(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ZipRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(build_test_zip()))
    book_id = insert_book_with_file(title="A Lemon Tree", filename="A Lemon Tree.zip", rd_link="https://rd.example/zip-link", streamable=0)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    target = tmp_path / "abs-import" / "A Lemon Tree"
    assert (target / "001 - 01 Opening.mp3").read_bytes() == b"mp3-audio"
    assert (target / "002 - 02 Next.m4b").read_bytes() == b"m4b-audio"
    assert not (target / "cover.jpg").exists()
    assert not (tmp_path / "abs-import" / "escape.mp3").exists()
    assert fake_abs.scans == [("lib-1", True)]


def test_audiobookshelf_import_rejects_unsafe_rd_direct_url(tmp_path):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: UnsafeStreamRealDebrid()
    book_id = insert_book_with_file(title="A Lemon Tree", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    assert fake_abs.scans == []
    assert b"127.0.0.1" not in response.data


def test_audiobookshelf_import_sanitizes_title_path(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    import_dir = tmp_path / "abs-import"
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(import_dir)
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: FakeRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(b"mp3-audio"))
    book_id = insert_book_with_file(title="../Bad: Book?", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 200
    imported = list(import_dir.rglob("*.mp3"))
    assert len(imported) == 1
    assert import_dir.resolve() in imported[0].resolve().parents
    assert not (tmp_path / "Bad_ Book_").exists()


def test_audiobookshelf_import_no_playable_audio_does_not_scan(tmp_path):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    book_id = insert_book_with_file(title="A Lemon Tree", filename="cover.jpg", streamable=0)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 409
    assert response.get_json()["message"] == "No playable audio files to import"
    assert fake_abs.scans == []


def test_audiobookshelf_import_scan_failure_is_sanitized(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    configure_abs(FailingAudiobookshelf())
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: FakeRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeZipDownloadResponse(b"mp3-audio"))
    book_id = insert_book_with_file(title="A Lemon Tree", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 502
    assert response.get_json()["message"] == "Audiobookshelf request failed"
    assert b"AUDIOBOOKSHELF_API_TOKEN" not in response.data
    assert b"abs-secret-token" not in response.data


def test_audiobookshelf_import_rejects_unsafe_direct_redirect(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: FakeRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeRedirectDownloadResponse())
    book_id = insert_book_with_file(title="A Lemon Tree", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    assert fake_abs.scans == []
    assert not list((tmp_path / "abs-import").rglob("*.mp3"))
    assert b"127.0.0.1" not in response.data


def test_audiobookshelf_import_rejects_unsafe_archive_redirect(tmp_path, monkeypatch):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["ZIP_CACHE_DIR"] = str(tmp_path / "zip-cache")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: ZipRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeRedirectDownloadResponse())
    book_id = insert_book_with_file(title="A Lemon Tree", filename="A Lemon Tree.zip", rd_link="https://rd.example/zip-link", streamable=0)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    assert fake_abs.scans == []
    assert not list((tmp_path / "abs-import").rglob("*.mp3"))
    assert b"127.0.0.1" not in response.data


def test_audiobookshelf_import_does_not_log_direct_download_token(tmp_path, monkeypatch, capsys):
    reset_db(tmp_path / "test.db")
    fake_abs = FakeAudiobookshelf()
    configure_abs(fake_abs)
    app_module.app.config["AUDIOBOOKSHELF_IMPORT_DIR"] = str(tmp_path / "abs-import")
    app_module.app.config["RD_CLIENT_FACTORY"] = lambda: FakeRealDebrid()
    monkeypatch.setattr(app_module.requests, "get", lambda *args, **kwargs: FakeFailingDownloadResponse())
    book_id = insert_book_with_file(title="A Lemon Tree", filename="Chapter 01.mp3", streamable=1)

    response = app_module.app.test_client().post(f"/api/books/{book_id}/import-to-audiobookshelf")
    captured = capsys.readouterr()

    assert response.status_code == 502
    assert response.get_json()["message"] == "Real-Debrid request failed"
    assert "download-secret" not in captured.out
    assert "download-secret" not in captured.err
    assert b"download-secret" not in response.data
