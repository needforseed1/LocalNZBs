# LocalNZBs

A minuscule Newznab-compatible server for exposing a local directory of `.nzb` files to AIOStreams, Prowlarr, or anything else that can query a Newznab indexer.

It is intended for setups where NZBs already exist locally, for example alongside nzbdavex/AIOStreams, but your search client still needs something that behaves like an indexer.

## Features

- Serves `/api?t=caps`, `/api?t=search`, `/api?t=tvsearch`, `/api?t=movie`, and `/api?t=get`.
- Recursively scans a local NZB directory.
- Parses filenames with `guessit` to infer movie/episode metadata.
- Parses NZB XML segment sizes so Newznab results report release payload size, not the `.nzb` file size.
- Emits Newznab RSS XML with category, title, year, season, episode, resolution, source, codec, and release group attrs when available.
- Streams the original NZB file back for `t=get`.
- Can optionally accept authenticated NZB uploads if sharing a directory is not practical.
- Keeps only parsed metadata in memory. The NZB files remain on disk and are the source of truth; no database is required.

## Run Locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
NZB_DIR=/path/to/nzbs PROVIDER_NAME=LocalNZBs uvicorn nzbserver.app:app --host 0.0.0.0 --port 8000
```

Then test:

```text
http://localhost:8000/api?t=caps
http://localhost:8000/api?t=search&q=some.title
```

## Docker

The example compose file pulls the published image from GitHub Container Registry:

```bash
docker compose -f docker-compose.example.yml up -d
```

The default image is `ghcr.io/needforseed1/localnzbs:latest`. To build locally instead, replace the `image:` line with `build: .`.

For local app settings and secrets, copy `.env.example` to `.env` and adjust the values for your system.

The compose file mounts `./nzbs` into the container as `/nzbs` and publishes `8000:8000`. Change those compose lines if your NZBs live somewhere else or if port 8000 is already used. The host side of the NZB mount should be the same directory where nzbdave/nzbdavex downloads or saves NZB files.

The example runs as `1000:1000` so generated files are not owned by root. Change the `user:` line in `docker-compose.example.yml` if a different user/group should read and write the shared NZB directory.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `NZB_DIR` | `/nzbs` | Directory containing `.nzb` files. Scanned recursively. |
| `PROVIDER_NAME` | `LocalNZBs` | Name exposed to clients as the provider/indexer. |
| `API_KEY` | unset | Optional key. If set, requests must include `apikey`. |
| `UPLOAD_KEY` | unset | Optional key for `PUT /nzb/{filename}` uploads. If unset, HTTP upload is disabled. |
| `BASE_URL` | request host | Optional public base URL used in generated download links. |
| `REFRESH_SECONDS` | `10` | Minimum seconds between directory rescans. |
| `MAX_UPLOAD_BYTES` | `104857600` | Maximum accepted raw NZB upload size. |
| `HOST` | `0.0.0.0` in Docker | Docker command host binding. |
| `PORT` | `8000` in Docker | Docker command port. |

## Same-Host Mode

Most setups should use a shared directory. Mount the same host directory into LocalNZBs and nzbdavex/nzbdave:

```yaml
services:
  nzbserver:
    volumes:
      - /srv/nzbs:/nzbs:rw

  nzbdavex:
    volumes:
      - /srv/nzbs:/output-nzbs:rw
```

Configure nzbdavex to save NZBs into `/output-nzbs` and keep LocalNZBs configured with `NZB_DIR=/nzbs`.

The host path must be the same on both mounts. In the example above, both containers point at `/srv/nzbs`, even though the path inside each container is different.

## Optional HTTP Upload

Use this only when sharing a host directory is not practical, for example when the process saving NZBs runs on another machine.

Configure nzbserver with an upload key:

```yaml
environment:
  NZB_DIR: /nzbs
  PROVIDER_NAME: LocalNZBs
  UPLOAD_KEY: some-long-secret
```

Then have nzbdavex, a post-save hook, a wrapper script, or a sidecar watcher push each saved NZB to nzbserver:

```bash
curl -f \
  -H "X-Upload-Key: $UPLOAD_KEY" \
  -H "Content-Type: application/x-nzb" \
  --upload-file "/path/to/file.nzb" \
  "http://nzbserver-host:8000/nzb/$(basename "/path/to/file.nzb")"
```

The upload contract is:

```text
PUT /nzb/{filename}
X-Upload-Key: secret
body: raw .nzb file
```

Response:

```json
{"ok": true, "filename": "Movie.Title.2023.1080p.WEB-DL.x265-GROUP.nzb", "size": 12345}
```

Uploads are written atomically, then the in-memory index is refreshed immediately. Re-uploading the same filename overwrites it, which makes retrying safe.

## Newznab Setup

Add LocalNZBs anywhere that accepts a Newznab source, including AIOStreams or Prowlarr:

- URL: `http://host:8000/api`
- API Path: leave as default if Prowlarr asks
- API Key: use the `API_KEY` value if configured, otherwise any value may be accepted depending on Prowlarr validation
- Categories: enable Movies and/or TV

Searches should return locally saved NZBs whose filenames match the requested title. TV searches also filter on season/episode when the client sends those parameters.

## Pin Local NZBs in AIOStreams

To keep local NZBs at the top of AIOStreams results, add this stream expression:

```text
pin(indexer(type(streams, 'usenet'), 'LocalNZBs'), 'top')
```

Add it in AIOStreams under:

```text
Filters -> Stream Expression -> Excluded
```

`LocalNZBs` must match the indexer name shown in AIOStreams results. This is the default `PROVIDER_NAME` used by nzbserver.

The expression belongs in `Excluded` stream expressions because `pin()` marks matching Usenet streams as pinned without removing them.

If local NZBs appear only after restarting AIOStreams, disable or shorten AIOStreams' built-in Prowlarr search cache. A cached Prowlarr result can be created before nzbdavex saves the local NZB, then AIOStreams keeps returning that stale result instead of asking Prowlarr again.

For the most reliable setup, use Prowlarr for remote Usenet indexers and add LocalNZBs directly to AIOStreams as a Newznab source. Disable `LocalNZBs` in the Prowlarr addon selection to avoid caching local and remote results together.

Example AIOStreams cache settings:

```env
BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900
BUILTIN_NAB_SEARCH_CACHE_TTL=0
```

`BUILTIN_PROWLARR_SEARCH_CACHE_TTL=900` keeps remote Prowlarr searches cached for 15 minutes, which is short enough for new episodes and better releases to appear soon without hammering remote indexers.

`BUILTIN_NAB_SEARCH_CACHE_TTL=0` makes direct Newznab searches against `nzbserver` fresh, so newly saved local NZBs can appear on the next search.

After changing these values, recreate AIOStreams so the environment is reloaded and the old in-memory cache is dropped:

```bash
docker compose up -d
```

## Filename Parsing

Metadata comes from the NZB filename, not from the NZB contents or an external metadata provider. Good release-style names work best:

```text
Some.Movie.2024.1080p.WEB-DL.x265-GROUP.nzb
Some.Show.S02E05.2160p.WEB-DL.x265-GROUP.nzb
```

If the title/year/season/episode are not recognizable from the filename, the item can still be returned by broad search, but Prowlarr matching may be weaker.
