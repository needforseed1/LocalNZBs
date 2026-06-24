# LocalNZBs

A minuscule Newznab-compatible server that exposes a local directory of `.nzb` files to AIOStreams, Prowlarr, or anything else that can query a Newznab indexer.

It is for setups where NZBs already exist locally — for example alongside nzbdavex/AIOStreams — but your search client still needs something that behaves like an indexer.

## Features

- Serves Newznab `caps`, `search`, `tvsearch`, `movie`, and `get` over `/api`. `GET /` also returns caps.
- Recursively scans a local NZB directory; keeps only parsed metadata in memory. The `.nzb` files on disk are the source of truth — no database.
- Parses filenames with `guessit` to infer movie/episode metadata and category.
- Reads NZB XML segment sizes so results report the release payload size, not the `.nzb` file size.
- Emits Newznab RSS with category, size, file count, title, year, season, episode, resolution, source, codec, release group, and more when available.

## Quick Start (Docker)

The example compose file pulls the published image from GitHub Container Registry and mounts `./nzbs` into the container as `/nzbs`:

```bash
cp .env.example .env      # then set a real API_KEY (see Configuration)
docker compose -f docker-compose.example.yml up -d
```

Or run it without compose:

```bash
docker run -d --name nzbserver -p 8000:8000 \
  -e API_KEY="$(openssl rand -hex 32)" \
  -v /srv/nzbs:/nzbs:ro \
  ghcr.io/needforseed1/localnzbs:latest
```

(`docker run` pulls the image automatically; use `docker pull ghcr.io/needforseed1/localnzbs:latest` to fetch updates.)

- Default image: `ghcr.io/needforseed1/localnzbs:latest`. To build locally, replace the `image:` line with `build: .`.
- Change the volume and `8000:8000` port lines if your NZBs live elsewhere or port 8000 is taken.
- The container runs as `1000:1000`. Change the `user:` line if a different user/group should read the shared directory.

### Shared directory

The host side of the `./nzbs` mount must be the **same directory** where nzbdave/nzbdavex saves downloaded NZBs. Mount that one host directory into both containers:

```yaml
services:
  nzbserver:
    volumes:
      - /srv/nzbs:/nzbs:ro

  nzbdavex:
    volumes:
      - /srv/nzbs:/output-nzbs:rw
```

LocalNZBs only reads the directory, so mount it read-only with `:ro`.

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
| `BASE_URL` | request host | Public base URL used in generated download links. |
| `REFRESH_SECONDS` | `10` | Minimum seconds between directory rescans. |
| `UI_PASSWORD` | _(unset)_ | Password for the web UI at `/ui`. The UI is disabled until this is set. |
| `TRASH_DIR` | `<NZB_DIR>/.trash` | Where deleted NZBs are moved. Excluded from indexing. |
| `HOST` | `0.0.0.0` in Docker | Docker command host binding. |
| `PORT` | `8000` in Docker | Docker command port. |

### Keys

`.env.example` ships `API_KEY` at a placeholder value (`change-me`). The server treats that placeholder as "not set" and fails closed:

- `API_KEY` left at `change-me` (or empty): `/api` returns `403` until you set a real key. No open access.

Generate a real secret with:

```bash
openssl rand -hex 32
```

Put the value in `.env` (or the container environment), then give the same `API_KEY` to Prowlarr/AIOStreams.

> Note: `GET /` returns caps without an API key. It exposes only the provider name (no NZB data) and is convenient as a health probe.

## Web UI

A built-in web interface lives under `/ui` for browsing indexed NZBs, inspecting per-release metadata, viewing stats, and (mass-)deleting releases.

- **Disabled by default.** It fails closed until `UI_PASSWORD` is set — same model as `API_KEY`. Sign in at `/ui/login` with that password.
- Auth is separate from `API_KEY`: the indexer key is for Prowlarr/AIOStreams, `UI_PASSWORD` is for the browser. The session is stored in a signed cookie.
- **Browse** (`/ui/`): filter by query, category, or media type; paginated; per-row and select-all checkboxes for bulk actions.
- **Stats** (`/ui/stats`): totals, breakdown by category/resolution/release group, largest and most-recent releases.
- **Delete moves to trash, it does not unlink.** Selected `.nzb` files are moved to `TRASH_DIR` (default `<NZB_DIR>/.trash`), which is excluded from indexing, so they vanish from search results but remain recoverable on disk. Empty the trash directory yourself when you are sure.

> **Requires a writable mount.** Deleting writes to the NZB directory, so the volume must be mounted `:rw` (the example compose file does this). The same directory is shared with nzbdave/nzbdavex and AIOStreams — deleting an NZB here removes the release those tools rely on. If you only want browsing/stats with no risk of modification, keep the mount `:ro`; delete actions will then fail and the on-disk files stay untouched.

## Client Setup

### Prowlarr / AIOStreams (Newznab source)

Add LocalNZBs anywhere that accepts a Newznab source:

- URL: `http://host:8000/api`
- API Path: leave as default if Prowlarr asks
- API Key: the `API_KEY` you set (required)
- Categories: enable Movies and/or TV

Searches return locally saved NZBs whose filenames match the requested title. TV searches also filter on season/episode when the client sends them.

### Pin local NZBs in AIOStreams

For best results, add LocalNZBs to AIOStreams directly as a Newznab source, and keep Prowlarr for remote Usenet indexers only.

That split keeps two independent cache paths: AIOStreams can cache slow remote Prowlarr searches while querying LocalNZBs through the direct Newznab path, which you can disable so newly saved local NZBs appear immediately (see [Caching](#caching)).

Routing LocalNZBs through the Prowlarr addon instead caches local and remote results together. AIOStreams can then keep serving a stale Prowlarr result that was cached before nzbdavex saved the local NZB.

Disable LocalNZBs in the AIOStreams Prowlarr addon selection, then add LocalNZBs to **Ranked Stream Expressions** with a high score, for example `100000`:

```text
/* LocalNZBs */ indexer(type(streams, 'usenet'), 'LocalNZBs')
```

This makes matching LocalNZBs streams sort above normal remote results.

Also add this stream expression under **Filters → Stream Expression → Excluded**:

```text
pin(indexer(type(streams, 'usenet'), 'LocalNZBs'), 'top')
```

The `pin()` expression belongs in *Excluded* because it marks matching Usenet streams as pinned without removing them. Do not put this expression in a required/included filter, or it can act like a filter instead of just pinning.

`LocalNZBs` must match the indexer name shown in AIOStreams results. By default, that comes from `PROVIDER_NAME=LocalNZBs`. If you rename the provider, update the expression to match the displayed indexer name.

### Caching

If local NZBs appear only after restarting AIOStreams, its Prowlarr search cache may have stored a result before nzbdavex saved the local NZB. Set these in the AIOStreams `.env` file or container environment, not in LocalNZBs:

```env
BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900
BUILTIN_NAB_SEARCH_CACHE_TTL=0
```

- `BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900` — keep remote searches cached 15 min: short enough for new releases, light on remote indexers.
- `BUILTIN_NAB_SEARCH_CACHE_TTL=0` — direct Newznab searches against LocalNZBs stay fresh, so newly saved NZBs appear on the next search.

Recreate AIOStreams after changing these so the old in-memory cache is dropped: `docker compose up -d`.

## Filename Parsing

Metadata comes from the NZB filename, not the NZB contents or any external provider. Release-style names work best:

```text
Some.Movie.2024.1080p.WEB-DL.x265-GROUP.nzb
Some.Show.S02E05.2160p.WEB-DL.x265-GROUP.nzb
```

If the title/year/season/episode are not recognizable from the filename, the item can still surface in broad search, but Prowlarr matching is weaker.
