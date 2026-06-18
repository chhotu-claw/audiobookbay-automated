import os
import sys
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


def reset_db(db_path):
    app_module.app.config.pop("RD_CLIENT_FACTORY", None)

    app_module.app.config["DATABASE_PATH"] = str(db_path)
    with app_module.app.app_context():
        db = app_module.get_db()
        db.executescript("DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS books;")
        db.commit()
        app_module.init_db()


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


def test_send_alias_removed(tmp_path):
    reset_db(tmp_path / "test.db")
    response = app_module.app.test_client().post("/send", json={"title": "Book", "link": SAFE_ABB_LINK})
    assert response.status_code == 404
