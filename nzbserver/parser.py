from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guessit import guessit


SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ParsedMetadata:
    title: str
    media_type: str
    category: int
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    resolution: str | None = None
    source: str | None = None
    video_codec: str | None = None
    release_group: str | None = None


def parse_filename(path: Path) -> ParsedMetadata:
    name = path.name.removesuffix(path.suffix)
    data = guessit(name)

    title = str(data.get("title") or readable_title(name))
    media_type = str(data.get("type") or "unknown")
    year = int_or_none(data.get("year"))
    season = first_int(data.get("season"))
    episode = first_int(data.get("episode"))
    resolution = str_or_none(data.get("screen_size"))

    if season is not None or episode is not None or media_type == "episode":
        media_type = "episode"
    elif year is not None or media_type == "movie":
        media_type = "movie"

    return ParsedMetadata(
        title=title,
        media_type=media_type,
        category=category_for(media_type, resolution),
        year=year,
        season=season,
        episode=episode,
        resolution=resolution,
        source=str_or_none(data.get("source")),
        video_codec=str_or_none(data.get("video_codec")),
        release_group=str_or_none(data.get("release_group")),
    )


def normalize(value: str | None) -> str:
    if not value:
        return ""
    return SEPARATOR_RE.sub(" ", value.lower()).strip()


def readable_title(value: str) -> str:
    return normalize(value).title() or value


def category_for(media_type: str, resolution: str | None) -> int:
    is_hd = resolution in {"720p", "1080p", "1080i", "2160p", "4k"}
    if media_type == "episode":
        return 5040 if is_hd else 5000
    if media_type == "movie":
        return 2040 if is_hd else 2000
    return 7000


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def first_int(value: Any) -> int | None:
    if isinstance(value, list):
        return int_or_none(value[0]) if value else None
    return int_or_none(value)


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
