from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from .parser import ParsedMetadata, normalize, parse_filename


CATEGORY_PARENTS = {
    2040: 2000,
    5040: 5000,
}

logger = logging.getLogger("nzbserver.index")


@dataclass(frozen=True)
class NzbItem:
    id: str
    path: Path
    filename: str
    size: int
    nzb_size: int
    file_count: int
    mtime: float
    metadata: ParsedMetadata
    searchable: str


@dataclass(frozen=True)
class SearchResults:
    items: list[NzbItem]
    offset: int
    total: int


@dataclass(frozen=True)
class IndexStats:
    total_count: int
    total_size: int
    by_category: dict[int, int]
    by_media_type: dict[str, int]
    by_resolution: dict[str, int]
    by_group: dict[str, int]
    size_by_category: dict[int, int]
    largest: list[NzbItem]
    newest: list[NzbItem]


@dataclass(frozen=True)
class DeleteResult:
    moved: list[str]
    missing: list[str]


class NzbIndex:
    def __init__(self, root: Path, refresh_seconds: int = 10) -> None:
        self.root = root
        self.refresh_seconds = refresh_seconds
        self._items: dict[str, NzbItem] = {}
        self._last_refresh = 0.0
        self._refresh_lock = threading.Lock()

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_refresh < self.refresh_seconds:
            return

        with self._refresh_lock:
            now = time.monotonic()
            if not force and now - self._last_refresh < self.refresh_seconds:
                return

            old_items = self._items
            items: dict[str, NzbItem] = {}
            if self.root.exists():
                try:
                    paths = sorted(self.root.rglob("*"))
                except OSError as exc:
                    logger.warning("failed to scan nzb directory root=%s error=%s", self.root, exc)
                    paths = []

                for path in paths:
                    try:
                        if not path.is_file() or path.suffix.lower() != ".nzb":
                            continue
                        if self._is_hidden(path):
                            continue

                        stat = path.stat()
                        item_id = stable_id(self.root, path)
                        old_item = old_items.get(item_id)
                        if (
                            old_item is not None
                            and old_item.path == path
                            and old_item.mtime == stat.st_mtime
                            and old_item.nzb_size == stat.st_size
                        ):
                            items[item_id] = old_item
                        else:
                            items[item_id] = self._item_from_path(path, item_id, stat)
                    except OSError as exc:
                        logger.warning("skipping unreadable nzb path=%s error=%s", path, exc)
                    except Exception:
                        logger.exception("skipping nzb after parse failure path=%s", path)

            self._items = items
            self._last_refresh = now

    def _is_hidden(self, path: Path) -> bool:
        # Skip dotfiles and anything inside a dot-directory (e.g. the trash dir),
        # so deleted-to-trash NZBs are never re-indexed.
        try:
            parts = path.relative_to(self.root).parts
        except ValueError:
            parts = path.parts
        return any(part.startswith(".") for part in parts)

    def get(self, item_id: str) -> NzbItem | None:
        self.refresh()
        return self._items.get(item_id)

    def all_items(self) -> list[NzbItem]:
        self.refresh()
        return sorted(self._items.values(), key=lambda item: item.mtime, reverse=True)

    def stats(self) -> "IndexStats":
        self.refresh()
        items = list(self._items.values())
        total_size = sum(item.size for item in items)
        by_category: dict[int, int] = {}
        by_media_type: dict[str, int] = {}
        by_resolution: dict[str, int] = {}
        by_group: dict[str, int] = {}
        size_by_category: dict[int, int] = {}
        for item in items:
            meta = item.metadata
            by_category[meta.category] = by_category.get(meta.category, 0) + 1
            size_by_category[meta.category] = size_by_category.get(meta.category, 0) + item.size
            by_media_type[meta.media_type] = by_media_type.get(meta.media_type, 0) + 1
            resolution = meta.resolution or "unknown"
            by_resolution[resolution] = by_resolution.get(resolution, 0) + 1
            if meta.release_group:
                by_group[meta.release_group] = by_group.get(meta.release_group, 0) + 1

        largest = sorted(items, key=lambda item: item.size, reverse=True)[:10]
        newest = sorted(items, key=lambda item: item.mtime, reverse=True)[:10]
        return IndexStats(
            total_count=len(items),
            total_size=total_size,
            by_category=by_category,
            by_media_type=by_media_type,
            by_resolution=by_resolution,
            by_group=by_group,
            size_by_category=size_by_category,
            largest=largest,
            newest=newest,
        )

    def delete_to_trash(self, ids: list[str], trash_dir: Path) -> "DeleteResult":
        self.refresh()
        moved: list[str] = []
        missing: list[str] = []
        for item_id in ids:
            item = self._items.get(item_id)
            if item is None or not item.path.exists():
                missing.append(item_id)
                continue
            try:
                dest = self._trash_destination(item, trash_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                item.path.replace(dest)
            except OSError as exc:
                logger.warning("failed to trash id=%s path=%s error=%s", item_id, item.path, exc)
                missing.append(item_id)
                continue
            self._items.pop(item_id, None)
            moved.append(item_id)
        return DeleteResult(moved=moved, missing=missing)

    def _trash_destination(self, item: NzbItem, trash_dir: Path) -> Path:
        try:
            relative = item.path.relative_to(self.root)
        except ValueError:
            relative = Path(item.filename)
        dest = trash_dir / relative
        if not dest.exists():
            return dest
        # Collision: append a numeric suffix before the .nzb extension.
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while True:
            candidate = dest.with_name(f"{stem}.{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def search(
        self,
        query: str | None = None,
        mode: str = "search",
        season: int | None = None,
        episode: int | None = None,
        year: int | None = None,
        categories: set[int] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> SearchResults:
        self.refresh()
        terms = normalize(query).split()
        results = sorted(self._items.values(), key=lambda item: item.mtime, reverse=True)

        if mode == "tvsearch":
            results = [item for item in results if item.metadata.media_type == "episode"]
        elif mode == "movie":
            results = [item for item in results if item.metadata.media_type == "movie"]

        if terms:
            results = [
                item
                for item in results
                if all(term in item.searchable for term in terms)
            ]

        if season is not None:
            results = [item for item in results if item.metadata.season == season]
        if episode is not None:
            results = [
                item
                for item in results
                if item.metadata.episode in {episode, None}
            ]
        if year is not None:
            results = [item for item in results if item.metadata.year == year]
        if categories:
            results = [item for item in results if category_matches(item.metadata.category, categories)]

        return SearchResults(
            items=results[offset : offset + limit],
            offset=offset,
            total=len(results),
        )

    def _item_from_path(
        self,
        path: Path,
        item_id: str | None = None,
        stat: os.stat_result | None = None,
    ) -> NzbItem:
        metadata = parse_filename(path)
        stat = stat or path.stat()
        nzb_stats = parse_nzb_stats(path)
        item_id = item_id or stable_id(self.root, path)
        searchable = normalize(" ".join([path.stem, metadata.title, str(metadata.year or "")]))
        return NzbItem(
            id=item_id,
            path=path,
            filename=path.name,
            size=nzb_stats.release_size or stat.st_size,
            nzb_size=stat.st_size,
            file_count=nzb_stats.file_count,
            mtime=stat.st_mtime,
            metadata=metadata,
            searchable=searchable,
        )


def stable_id(root: Path, path: Path) -> str:
    try:
        value = str(path.relative_to(root))
    except ValueError:
        value = str(path)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class NzbStats:
    release_size: int
    file_count: int


def parse_nzb_stats(path: Path) -> NzbStats:
    release_size = 0
    file_count = 0
    try:
        for event, element in ET.iterparse(path, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "segment":
                release_size += parse_int(element.attrib.get("bytes")) or 0
            elif tag == "file":
                file_count += 1
            element.clear()
    except ET.ParseError:
        return NzbStats(release_size=0, file_count=1)
    return NzbStats(release_size=release_size, file_count=max(file_count, 1))


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_categories(value: str | None) -> set[int] | None:
    if not value:
        return None
    categories: set[int] = set()
    for part in value.split(","):
        category = parse_int(part.strip())
        if category is not None:
            categories.add(category)
    return categories or None


def category_matches(category: int, requested: set[int]) -> bool:
    return category in requested or CATEGORY_PARENTS.get(category) in requested
