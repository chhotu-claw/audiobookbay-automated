# AudiobookBay Automated Real-Debrid

A small Flask web app for searching AudioBookBay, adding selected audiobook magnets to Real-Debrid, tracking their Real-Debrid status in a local SQLite library, and playing completed audio files in the browser.

## Features

- Search AudioBookBay by title/keyword.
- Add a selected result to Real-Debrid using a server-side private API token.
- Persist the library in SQLite (`books` and `files` tables).
- Library page shows queued/downloading/downloaded/error status and progress.
- Per-book sync refreshes Real-Debrid torrent status and discovered files.
- Player page lists streamable files and uses an HTML5 `<audio>` player.
- `/stream/<file_id>` unrestricts the Real-Debrid link server-side and redirects the browser to the direct Real-Debrid URL. The app does not proxy audio bytes.

## Environment variables

```env
REAL_DEBRID_API_TOKEN=your_private_real_debrid_api_token  # required
ABB_HOSTNAME=audiobookbay.lu                              # optional
PAGE_LIMIT=5                                              # optional
DATABASE_PATH=/data/audiobooks.db                         # optional, defaults to ./data/audiobooks.db locally; compose sets /data/audiobooks.db
FLASK_PORT=5078                                           # optional
```

No OAuth flow is implemented; this is intended for a private/self-hosted app using a Real-Debrid private API token. Keep `REAL_DEBRID_API_TOKEN` server-side only.

## Docker Compose

```bash
cp .env.example .env  # if you keep one locally, otherwise create .env manually
docker compose up -d --build
```

The included `docker-compose.yaml` mounts `./data` to `/data` so the SQLite database persists across container restarts.

## Local development

```bash
cd app
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
REAL_DEBRID_API_TOKEN=... DATABASE_PATH=../data/audiobooks.db python app.py
```

Open http://localhost:5078.

## Usage

1. Search for an audiobook on the Search page.
2. Click **Add to Real-Debrid** on the desired result.
3. Open **Library** to view all added books.
4. Click **Sync** on a book after Real-Debrid has downloaded it.
5. Open the player page and play any streamable audio file.

## Notes

- This app does not run a torrent client and does not store downloaded audiobook files.
- Real-Debrid direct links are generated on demand by `/stream/<file_id>` and returned as redirects.
- AudioBookBay scraping can break if the site changes its markup or blocks requests.

## Tests

Tests mock AudioBookBay scraping and Real-Debrid calls:

```bash
cd app
pytest
```
