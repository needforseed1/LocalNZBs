from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime

from .index import NzbItem


NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
NEWZNAB = f"{{{NEWZNAB_NS}}}"

ET.register_namespace("newznab", NEWZNAB_NS)


def caps_xml(provider_name: str) -> bytes:
    caps = ET.Element("caps")
    ET.SubElement(
        caps,
        "server",
        {
            "title": provider_name,
            "strapline": "Local NZB Newznab bridge",
            "email": "",
            "url": "",
            "version": "0.1.0",
        },
    )
    ET.SubElement(caps, "limits", {"default": "100", "max": "100"})
    ET.SubElement(caps, "registration", {"available": "no", "open": "no"})

    searching = ET.SubElement(caps, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q,cat"})
    ET.SubElement(
        searching,
        "tv-search",
        {"available": "yes", "supportedParams": "q,season,ep,cat"},
    )
    ET.SubElement(
        searching,
        "movie-search",
        {"available": "yes", "supportedParams": "q,year,cat"},
    )

    categories = ET.SubElement(caps, "categories")
    movie = ET.SubElement(categories, "category", {"id": "2000", "name": "Movies"})
    ET.SubElement(movie, "subcat", {"id": "2040", "name": "Movies HD"})
    tv = ET.SubElement(categories, "category", {"id": "5000", "name": "TV"})
    ET.SubElement(tv, "subcat", {"id": "5040", "name": "TV HD"})
    ET.SubElement(categories, "category", {"id": "7000", "name": "Other"})

    return xml_bytes(caps)


def error_xml(code: int, description: str) -> bytes:
    error = ET.Element("error", {"code": str(code), "description": description})
    return xml_bytes(error)


def rss_xml(
    items: list[NzbItem],
    provider_name: str,
    base_url: str,
    request_path: str = "/api",
    api_key: str | None = None,
    *,
    offset: int = 0,
    total: int | None = None,
) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = provider_name
    ET.SubElement(channel, "description").text = "Local NZB Newznab bridge"
    ET.SubElement(channel, "link").text = api_url(base_url, request_path)
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(
        channel,
        f"{NEWZNAB}response",
        {"offset": str(offset), "total": str(len(items) if total is None else total)},
    )

    for item in items:
        channel.append(item_xml(item, provider_name, base_url, request_path, api_key))

    return xml_bytes(rss)


def item_xml(
    item: NzbItem,
    provider_name: str,
    base_url: str,
    request_path: str,
    api_key: str | None = None,
) -> ET.Element:
    metadata = item.metadata
    nzb_url = f"{api_url(base_url, request_path)}?t=get&id={item.id}"
    if api_key:
        nzb_url = f"{nzb_url}&apikey={api_key}"
    element = ET.Element("item")

    ET.SubElement(element, "title").text = item.filename.removesuffix(".nzb")
    ET.SubElement(element, "guid", {"isPermaLink": "false"}).text = item.id
    ET.SubElement(element, "link").text = nzb_url
    ET.SubElement(element, "comments").text = nzb_url
    ET.SubElement(element, "pubDate").text = format_datetime(
        datetime.fromtimestamp(item.mtime, tz=timezone.utc), usegmt=True
    )
    ET.SubElement(element, "category").text = str(metadata.category)
    ET.SubElement(
        element,
        "enclosure",
        {
            "url": nzb_url,
            "length": str(item.size),
            "type": "application/x-nzb",
        },
    )

    add_attr(element, "category", metadata.category)
    add_attr(element, "size", item.size)
    add_attr(element, "files", item.file_count)
    add_attr(element, "grabs", 0)
    add_attr(element, "nzbsize", item.nzb_size)
    add_attr(element, "provider", provider_name)
    add_attr(element, "guid", item.id)
    add_attr(element, "title", metadata.title)
    add_attr(element, "mediatype", metadata.media_type)
    add_attr(element, "year", metadata.year)
    add_attr(element, "season", metadata.season)
    add_attr(element, "episode", metadata.episode)
    add_attr(element, "resolution", metadata.resolution)
    add_attr(element, "source", metadata.source)
    add_attr(element, "video_codec", metadata.video_codec)
    add_attr(element, "release_group", metadata.release_group)

    return element


def add_attr(element: ET.Element, name: str, value: object | None) -> None:
    if value is None or value == "":
        return
    ET.SubElement(element, f"{NEWZNAB}attr", {"name": name, "value": str(value)})


def api_url(base_url: str, request_path: str) -> str:
    return f"{base_url.rstrip('/')}{request_path}"


def xml_bytes(element: ET.Element) -> bytes:
    ET.indent(element)
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)
