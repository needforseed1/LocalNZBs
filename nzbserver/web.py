from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import app as app_module
from .index import NzbItem
from .parser import normalize

router = APIRouter(prefix="/ui")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CATEGORY_NAMES = {
    2000: "Movies",
    2040: "Movies HD",
    5000: "TV",
    5040: "TV HD",
    7000: "Other",
}

PER_PAGE = 50

SORT_KEYS = {
    "title": lambda item: item.filename.lower(),
    "category": lambda item: item.metadata.category,
    "type": lambda item: item.metadata.media_type,
    "resolution": lambda item: item.metadata.resolution or "",
    "size": lambda item: item.size,
    "files": lambda item: item.file_count,
    "mtime": lambda item: item.mtime,
}


def category_name(category: int) -> str:
    return CATEGORY_NAMES.get(category, str(category))


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


templates.env.filters["human_size"] = human_size
templates.env.filters["category_name"] = category_name
templates.env.filters["dt"] = format_mtime


def ui_enabled() -> bool:
    return app_module.settings.ui_password is not None


def is_authed(request: Request) -> bool:
    return bool(request.session.get("authed"))


def require_ui(request: Request) -> Response | None:
    """Return a Response to short-circuit (disabled/redirect), or None if allowed."""
    if not ui_enabled():
        return HTMLResponse(
            "<h1>UI disabled</h1><p>Set <code>UI_PASSWORD</code> to enable the web interface.</p>",
            status_code=404,
        )
    if not is_authed(request):
        return RedirectResponse(url="/ui/login", status_code=303)
    return None


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    if not ui_enabled():
        return HTMLResponse("<h1>UI disabled</h1>", status_code=404)
    if is_authed(request):
        return RedirectResponse(url="/ui/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"provider": app_module.settings.provider_name, "error": None}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form("")) -> Response:
    if not ui_enabled():
        return HTMLResponse("<h1>UI disabled</h1>", status_code=404)
    expected = app_module.settings.ui_password or ""
    if hmac.compare_digest(password, expected):
        request.session["authed"] = True
        return RedirectResponse(url="/ui/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"provider": app_module.settings.provider_name, "error": "Incorrect password"},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/ui/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def list_items(
    request: Request,
    q: str = "",
    cat: str = "",
    type: str = "",
    page: int = 1,
    sort: str = "mtime",
    dir: str = "desc",
    msg: str = "",
) -> Response:
    blocked = require_ui(request)
    if blocked is not None:
        return blocked

    items = app_module.index.all_items()
    items = _filter_items(items, q=q, cat=cat, media_type=type)

    sort = sort if sort in SORT_KEYS else "mtime"
    dir = "asc" if dir == "asc" else "desc"
    items = sorted(items, key=SORT_KEYS[sort], reverse=(dir == "desc"))

    total = len(items)
    page = max(page, 1)
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    page = min(page, pages)
    start = (page - 1) * PER_PAGE
    visible = items[start : start + PER_PAGE]

    return templates.TemplateResponse(
        request,
        "list.html",
        {
            "provider": app_module.settings.provider_name,
            "items": visible,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
            "cat": cat,
            "type": type,
            "sort": sort,
            "dir": dir,
            "msg": msg,
            "categories": CATEGORY_NAMES,
        },
    )


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request) -> Response:
    blocked = require_ui(request)
    if blocked is not None:
        return blocked

    stats = app_module.index.stats()
    top_groups = sorted(stats.by_group.items(), key=lambda kv: kv[1], reverse=True)[:15]
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "provider": app_module.settings.provider_name,
            "stats": stats,
            "top_groups": top_groups,
        },
    )


@router.get("/item/{item_id}", response_class=HTMLResponse)
def item_detail(request: Request, item_id: str) -> Response:
    blocked = require_ui(request)
    if blocked is not None:
        return blocked

    item = app_module.index.get(item_id)
    if item is None:
        return HTMLResponse("<h1>404</h1><p>NZB not found.</p>", status_code=404)
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "provider": app_module.settings.provider_name,
            "item": item,
            "api_key": app_module.settings.api_key,
        },
    )


@router.post("/delete")
def delete_items(request: Request, ids: list[str] = Form(default=[])) -> Response:
    blocked = require_ui(request)
    if blocked is not None:
        return blocked

    result = app_module.index.delete_to_trash(ids, app_module.settings.trash_dir)
    moved = len(result.moved)
    missing = len(result.missing)
    msg = f"Moved {moved} NZB(s) to trash."
    if missing:
        msg += f" {missing} could not be found."
    url = f"/ui/?msg={msg}"
    return RedirectResponse(url=url, status_code=303)


def _filter_items(
    items: list[NzbItem], q: str = "", cat: str = "", media_type: str = ""
) -> list[NzbItem]:
    terms = normalize(q).split()
    if terms:
        items = [item for item in items if all(term in item.searchable for term in terms)]
    if cat:
        try:
            wanted = int(cat)
            items = [item for item in items if item.metadata.category == wanted]
        except ValueError:
            pass
    if media_type:
        items = [item for item in items if item.metadata.media_type == media_type]
    return items
