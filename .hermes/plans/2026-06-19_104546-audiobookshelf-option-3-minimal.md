# Audiobookshelf Option 3 Minimal Integration Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Take the smallest safe step toward one Android app backed by a Flask acquisition sidecar and stock Audiobookshelf as the library/playback engine.

**Architecture:** Keep the current Flask and Android playback path intact. Add only server-side Audiobookshelf configuration, a tiny API client, status endpoint, and scan trigger endpoint. This proves Flask can securely talk to Audiobookshelf before we import books or rewrite Android playback.

**Tech Stack:** Flask, SQLite, pytest, Docker Compose, Audiobookshelf HTTP API, Android Java later.

---

## Non-goals for this step

- Do not deploy Audiobookshelf in Compose yet.
- Do not rewrite Android playback yet.
- Do not move books into an Audiobookshelf library yet.
- Do not remove existing `/api/library`, `/api/books`, `/stream`, or `/stream-zip` behavior.
- Do not expose Real-Debrid or Audiobookshelf tokens to Android.

## Task 1: Add server-side Audiobookshelf config and client

**Objective:** Add optional ABS configuration and a minimal authenticated client without changing existing behavior.

**Files:**
- Modify: `app/app.py`
- Test: `app/tests/test_rd_app.py`

**Implementation notes:**
- Add env vars:
  - `AUDIOBOOKSHELF_URL`
  - `AUDIOBOOKSHELF_API_TOKEN`
  - `AUDIOBOOKSHELF_LIBRARY_ID`
  - `AUDIOBOOKSHELF_IMPORT_DIR`
- Add `AudiobookshelfError` and `AudiobookshelfClient` next to `RealDebridClient`.
- Add `abs_client()` factory using `app.config["ABS_CLIENT_FACTORY"]` for tests.
- Do not print token values.

**Verification:**
- Unit tests use a fake ABS client.
- No real ABS network calls during tests.

## Task 2: Add read-only integration status endpoint

**Objective:** Allow Android/web to check whether ABS is configured and reachable without leaking credentials.

**Files:**
- Modify: `app/app.py`
- Test: `app/tests/test_rd_app.py`

**Endpoint:**

```http
GET /api/audiobookshelf/status
```

**Response when unconfigured:**

```json
{
  "configured": false,
  "reachable": false,
  "authorized": false,
  "library_found": false,
  "import_dir_configured": false
}
```

**Response when configured and fake client succeeds:**

```json
{
  "configured": true,
  "url": "http://abs.local",
  "library_id": "lib-1",
  "import_dir_configured": true,
  "reachable": true,
  "authorized": true,
  "library_found": true,
  "library": {"id": "lib-1", "name": "Audiobooks", "mediaType": "book"}
}
```

**Security:**
- Response must not include `AUDIOBOOKSHELF_API_TOKEN` or token-like provider details.

## Task 3: Add scan trigger endpoint

**Objective:** Add the smallest useful control action for future imports: trigger a configured ABS library scan.

**Files:**
- Modify: `app/app.py`
- Test: `app/tests/test_rd_app.py`

**Endpoint:**

```http
POST /api/audiobookshelf/scan
```

**Request:**

```json
{"force": false}
```

**Response:**

```json
{
  "message": "Audiobookshelf scan started",
  "library_id": "lib-1"
}
```

**Failure:**
- Return `400` if ABS is not fully configured.
- Return sanitized `502` if ABS API call fails.

## Task 4: Wire optional Docker env and docs

**Objective:** Make the scaffold configurable without changing default behavior.

**Files:**
- Modify: `docker-compose.yaml`
- Modify: `README.md`

**Compose env additions:**

```yaml
- AUDIOBOOKSHELF_URL=${AUDIOBOOKSHELF_URL:-}
- AUDIOBOOKSHELF_API_TOKEN=${AUDIOBOOKSHELF_API_TOKEN:-}
- AUDIOBOOKSHELF_LIBRARY_ID=${AUDIOBOOKSHELF_LIBRARY_ID:-}
- AUDIOBOOKSHELF_IMPORT_DIR=${AUDIOBOOKSHELF_IMPORT_DIR:-}
```

**Docs:**
- Explain this is optional and server-side only.
- Explain this step only verifies/scans ABS; import/playback migration comes next.

## Task 5: Verify and ship

**Objective:** Prove the minimal integration scaffold is safe.

**Commands:**

```bash
cd /home/chhotu/audiobookbay-automated-rd
uv run --with pytest --with flask --with requests --with beautifulsoup4 --with python-dotenv pytest -q
```

Expected: all tests pass.

```bash
docker compose config >/tmp/audiobookshelf-compose-check.yaml
```

Expected: exit code 0.

**Review:**
- Diff should be small.
- No Android rebuild needed.
- No token in responses/tests/docs.

## Next step after this plan

Only after this scaffold works: add `POST /api/books/<id>/import-to-audiobookshelf` that downloads/extracts one completed book into `AUDIOBOOKSHELF_IMPORT_DIR`, then triggers scan. That is intentionally not included in this first minimal change.
