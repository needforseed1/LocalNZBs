from __future__ import annotations

import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .index import NzbIndex, parse_categories, parse_int
from .newznab import caps_xml, error_xml, rss_xml


@dataclass(frozen=True)
class Settings:
    nzb_dir: Path
    provider_name: str
    api_key: str | None
    upload_key: str | None
    base_url: str | None
    refresh_seconds: int
    max_upload_bytes: int


# Keys left at their .env.example placeholder value are treated as "not set",
# so the server fails closed instead of running with a guessable secret.
PLACEHOLDER_KEYS = {"change-me", "change-me-too"}


def configured_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value in PLACEHOLDER_KEYS:
        return None
    return value


def load_settings() -> Settings:
    return Settings(
        nzb_dir=Path(os.getenv("NZB_DIR", "/nzbs")).expanduser(),
        provider_name=os.getenv("PROVIDER_NAME", "LocalNZBs"),
        api_key=configured_key(os.getenv("API_KEY")),
        upload_key=configured_key(os.getenv("UPLOAD_KEY")),
        base_url=os.getenv("BASE_URL") or None,
        refresh_seconds=int(os.getenv("REFRESH_SECONDS", "10")),
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
    )


settings = load_settings()
index = NzbIndex(settings.nzb_dir, refresh_seconds=settings.refresh_seconds)
logger = logging.getLogger("nzbserver")


def prewarm_index() -> None:
    try:
        index.refresh(force=True)
    except Exception:
        logger.exception("index prewarm failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(
        target=prewarm_index,
        name="nzbserver-index-prewarm",
        daemon=True,
    ).start()
    yield


app = FastAPI(title="nzbserver", version="0.1.0", lifespan=lifespan)


@app.get("/")
def root() -> Response:
    return xml_response(caps_xml(settings.provider_name))


@app.get("/api")
def api(request: Request) -> Response:
    params = request.query_params
    if not settings.api_key:
        return xml_response(
            error_xml(100, "API_KEY is not configured on the server"), status_code=403
        )
    if params.get("apikey") != settings.api_key:
        return xml_response(error_xml(100, "Incorrect user credentials"), status_code=403)

    command = params.get("t", "caps").lower()
    if command == "caps":
        return xml_response(caps_xml(settings.provider_name))
    if command == "get":
        return get_nzb(params.get("id"))
    if command in {"search", "tvsearch", "movie"}:
        return search(request, command)

    return xml_response(error_xml(203, f"Function not available: {command}"), status_code=400)


@app.put("/nzb/{filename}")
async def upload_nzb(filename: str, request: Request) -> JSONResponse:
    if not settings.upload_key:
        return json_error("NZB upload is disabled because UPLOAD_KEY is not set", 403)
    if request.headers.get("x-upload-key") != settings.upload_key:
        return json_error("Invalid upload key", 403)

    safe_name = safe_nzb_filename(filename)
    if safe_name is None:
        return json_error("filename must be a plain .nzb basename", 400)

    settings.nzb_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.nzb_dir / safe_name
    temp_path: Path | None = None
    size = 0

    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=settings.nzb_dir,
            prefix=f".{safe_name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    return json_error("NZB upload is too large", 413)
                temp_file.write(chunk)

        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    index.refresh(force=True)
    logger.info(
        "uploaded nzb filename=%s size=%s source=%s",
        safe_name,
        size,
        request.client.host if request.client else "unknown",
    )
    return JSONResponse({"ok": True, "filename": safe_name, "size": size})


def get_nzb(item_id: str | None) -> Response:
    if not item_id:
        return xml_response(error_xml(200, "Missing parameter: id"), status_code=400)

    item = index.get(item_id)
    if item is None or not item.path.exists():
        return xml_response(error_xml(300, "NZB not found"), status_code=404)

    return FileResponse(item.path, media_type="application/x-nzb", filename=item.filename)


def search(request: Request, command: str) -> Response:
    params = request.query_params
    limit = clamp(parse_int(params.get("limit")) or 100, 1, 100)
    offset = max(parse_int(params.get("offset")) or 0, 0)
    results = index.search(
        query=params.get("q"),
        mode=command,
        season=parse_first_int(params, "season", "s"),
        episode=parse_first_int(params, "ep", "episode", "e"),
        year=parse_int(params.get("year")),
        categories=parse_categories(params.get("cat")),
        offset=offset,
        limit=limit,
    )
    return xml_response(
        rss_xml(
            results.items,
            settings.provider_name,
            base_url_for(request),
            request.url.path,
            settings.api_key,
            offset=results.offset,
            total=results.total,
        )
    )


def base_url_for(request: Request) -> str:
    if settings.base_url:
        return settings.base_url
    return f"{request.url.scheme}://{request.url.netloc}"


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def parse_first_int(params: object, *names: str) -> int | None:
    for name in names:
        value = parse_int(params.get(name))
        if value is not None:
            return value
    return None


def safe_nzb_filename(filename: str) -> str | None:
    if not filename or filename in {".", ".."}:
        return None
    if "/" in filename or "\\" in filename:
        return None
    if Path(filename).name != filename:
        return None
    if not filename.lower().endswith(".nzb"):
        return None
    return filename


def xml_response(content: bytes, status_code: int = 200) -> Response:
    return Response(content=content, status_code=status_code, media_type="application/xml")


def json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)
