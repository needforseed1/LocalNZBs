# LocalNZBs

A minuscule Newznab-compatible server that exposes a local directory of `.nzb` files to AIOStreams, Prowlarr, or anything else that can query a Newznab indexer.

It is for setups where NZBs already exist locally — for example alongside nzbdavex/AIOStreams — but your search client still needs something that behaves like an indexer.

## Features

- Serves Newznab `caps`, `search`, `tvsearch`, `movie`, and `get` over `/api`. `GET /` also returns caps.
- Recursively scans a local NZB directory; keeps only parsed metadata in memory. The `.nzb` files on disk are the source of truth — no database.
- Parses filenames with `guessit` to infer movie/episode metadata and category.
- Reads NZB XML segment sizes so results report the release payload size, not the `.nzb` file size.
- Emits Newznab RSS with category, size, file count, title, year, season, episode, resolution, source, codec, release group, and more when available.
- Optional authenticated HTTP upload when sharing a directory is not practical.

## Quick Start (Docker)

The example compose file pulls the published image from GitHub Container Registry and mounts `./nzbs` into the container as `/nzbs`:

```bash
cp .env.example .env      # then set a real API_KEY (see Configuration)
docker compose -f docker-compose.example.yml up -d
```

- Default image: `ghcr.io/needforseed1/localnzbs:latest`. To build locally, replace the `image:` line with `build: .`.
- The host side of the `./nzbs` mount should be the **same directory** where nzbdave/nzbdavex saves downloaded NZBs. Change the volume and `8000:8000` port lines if your NZBs live elsewhere or port 8000 is taken.
- The container runs as `1000:1000` so files are not owned by root. Change the `user:` line if a different user/group should read and write the shared directory.

### Shared directory

Mount the same host directory into both LocalNZBs and nzbdavex/nzbdave:

```yaml
services:
  nzbserver:
    volumes:
      - /srv/nzbs:/nzbs:rw

  nzbdavex:
    volumes:
      - /srv/nzbs:/output-nzbs:rw
```

The container-side paths may differ, but the **host path must match** (`/srv/nzbs` above). Configure nzbdavex to save NZBs into `/output-nzbs`; keep LocalNZBs on `NZB_DIR=/nzbs`.

## Run from Source

Requires Python 3.11+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
API_KEY=$(openssl rand -hex 32) NZB_DIR=/path/to/nzbs uvicorn nzbserver.app:app --host 0.0.0.0 --port 8000
```

Then query:

```text
http://localhost:8000/api?t=caps&apikey=YOUR_KEY
http://localhost:8000/api?t=search&q=some.title&apikey=YOUR_KEY
http://localhost:8000/api?t=get&id=ITEM_ID&apikey=YOUR_KEY
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `NZB_DIR` | `/nzbs` | Directory containing `.nzb` files. Scanned recursively. |
| `PROVIDER_NAME` | `LocalNZBs` | Name exposed to clients as the provider/indexer. |
| `API_KEY` | `change-me` | Required. `/api` returns 403 until this is changed from `change-me`. Requests must then include `apikey`. |
| `UPLOAD_KEY` | `change-me-too` | Key for `PUT /nzb/{filename}` uploads. Upload stays disabled while unset or left at `change-me-too`. |
| `BASE_URL` | request host | Public base URL used in generated download links. |
| `REFRESH_SECONDS` | `10` | Minimum seconds between directory rescans. |
| `MAX_UPLOAD_BYTES` | `104857600` | Maximum accepted raw NZB upload size (100 MiB). |
| `HOST` | `0.0.0.0` in Docker | Docker command host binding. |
| `PORT` | `8000` in Docker | Docker command port. |

### Keys

`.env.example` ships both keys at placeholder values (`change-me`, `change-me-too`). The server treats those placeholders as "not set" and fails closed:

- `API_KEY` left at `change-me` (or empty): `/api` returns `403` until you set a real key. No open access.
- `UPLOAD_KEY` left at `change-me-too` (or empty): `PUT /nzb/{filename}` stays disabled.

Generate a real secret for either with:

```bash
openssl rand -hex 32
```

Put the value in `.env` (or the container environment), then give the same `API_KEY` to Prowlarr/AIOStreams.

> Note: `GET /` returns caps without an API key. It exposes only the provider name (no NZB data) and is convenient as a health probe.

## Client Setup

### Prowlarr / AIOStreams (Newznab source)

Add LocalNZBs anywhere that accepts a Newznab source:

- URL: `http://host:8000/api`
- API Path: leave as default if Prowlarr asks
- API Key: the `API_KEY` you set (required)
- Categories: enable Movies and/or TV

Searches return locally saved NZBs whose filenames match the requested title. TV searches also filter on season/episode when the client sends them.

### Pin local NZBs in AIOStreams

To keep local NZBs at the top of AIOStreams results, add this stream expression under
**Filters → Stream Expression → Excluded**:

```text
pin(indexer(type(streams, 'usenet'), 'LocalNZBs'), 'top')
```

It belongs in *Excluded* because `pin()` marks matching Usenet streams as pinned without removing them. `LocalNZBs` must match the indexer name shown in AIOStreams results (the default `PROVIDER_NAME`).

For the most reliable setup, use Prowlarr for remote Usenet indexers and add LocalNZBs directly to AIOStreams as a Newznab source. Disable `LocalNZBs` in the Prowlarr addon selection so local and remote results are not cached together.

### Caching

If local NZBs appear only after restarting AIOStreams, its Prowlarr search cache may have stored a result before nzbdavex saved the local NZB. Disable or shorten that cache:

```env
BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900
BUILTIN_NAB_SEARCH_CACHE_TTL=0
```

- `BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900` — keep remote searches cached 15 min: short enough for new releases, light on remote indexers.
- `BUILTIN_NAB_SEARCH_CACHE_TTL=0` — direct Newznab searches against LocalNZBs stay fresh, so newly saved NZBs appear on the next search.

Recreate AIOStreams after changing these so the old in-memory cache is dropped: `docker compose up -d`.

## Optional HTTP Upload

Use this only when sharing a host directory is not practical — for example when the process saving NZBs runs on another machine.

Set an upload key in `.env`:

```env
UPLOAD_KEY=$(openssl rand -hex 32)
```

The `/nzbs` volume must be writable, since uploads are saved there. Then have nzbdavex, a post-save hook, a wrapper script, or a sidecar watcher push each saved NZB:

```bash
curl -f \
  -H "X-Upload-Key: $UPLOAD_KEY" \
  -H "Content-Type: application/x-nzb" \
  --upload-file "/path/to/file.nzb" \
  "http://nzbserver-host:8000/nzb/$(basename "/path/to/file.nzb")"
```

Contract:

```text
PUT /nzb/{filename}
X-Upload-Key: secret
body: raw .nzb file
```

Response:

```json
{"ok": true, "filename": "Movie.Title.2023.1080p.WEB-DL.x265-GROUP.nzb", "size": 12345}
```

Uploads are written atomically, then the in-memory index refreshes immediately. Re-uploading the same filename overwrites it, so retrying is safe.

## Filename Parsing

Metadata comes from the NZB filename, not the NZB contents or any external provider. Release-style names work best:

```text
Some.Movie.2024.1080p.WEB-DL.x265-GROUP.nzb
Some.Show.S02E05.2160p.WEB-DL.x265-GROUP.nzb
```

If the title/year/season/episode are not recognizable from the filename, the item can still surface in broad search, but Prowlarr matching is weaker.
