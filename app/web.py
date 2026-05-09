"""Server-rendered dashboard routes (FR-09).

All pages work without JavaScript: forms post to the same URL the HTMX
fragment uses, and the full-page templates always render the table inline.
HTMX takes over on `hx-trigger=change` so filter changes update only the
relevant section.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, RedirectResponse

from . import (
    aliases as aliases_mod,
    auth, comparison,
    exercises as exercises_mod,
    jobs, leaderboard, repos,
)


TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


router = APIRouter()


WINDOWS = [("all", "All time"), ("90d", "Last 90 days"),
           ("30d", "Last 30 days"), ("7d", "Last 7 days")]


def _common(request: Request) -> dict:
    return {
        "request": request,
        "user": auth.session_user(request),
    }


@router.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/leaderboard", status_code=303)


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page(request: Request, window: str = "all",
                     exercise: str = ""):
    rows = _leaderboard_rows(request, window, exercise)
    ctx = _common(request) | {
        "rows": rows,
        "window": window if window in dict(WINDOWS) else "all",
        "exercise": exercise,
        "windows": WINDOWS,
        "exercises": exercises_mod.list_exercises(request.app.state.db),
    }
    return templates.TemplateResponse(request, "leaderboard.html", ctx)


@router.get("/leaderboard/table", response_class=HTMLResponse)
def leaderboard_table(request: Request, window: str = "all",
                      exercise: str = ""):
    """HTMX partial: the leaderboard table only."""
    rows = _leaderboard_rows(request, window, exercise)
    return templates.TemplateResponse(request, "_leaderboard_table.html",
                                       {"rows": rows})


def _leaderboard_rows(request: Request, window: str, exercise: str):
    win = window if window in dict(WINDOWS) else "all"
    ex_arg: tuple[str, str] | None = None
    if exercise:
        try:
            ref_type, name = exercise.split(":", 1)
        except ValueError:
            ref_type, name = "branch", exercise
        ex_arg = (name, ref_type)
    return leaderboard.compute_leaderboard(
        request.app.state.db, window=win, exercise=ex_arg,
    )


@router.get("/exercises", response_class=HTMLResponse)
def exercises_page(request: Request):
    ctx = _common(request) | {
        "exercises": exercises_mod.list_exercises(request.app.state.db),
    }
    return templates.TemplateResponse(request, "exercises.html", ctx)


@router.get("/exercises/{ref_type}/compare/{name:path}",
             response_class=HTMLResponse)
def comparison_page(request: Request, ref_type: str, name: str):
    """Compare participant solutions for one exercise.

    The `name` parameter uses the `:path` converter so branch names with
    slashes (e.g. `feature/day2`, `bugfix/login`) match this route. The
    literal segment `compare/` is placed before `name` so the converter
    isn't greedy across additional URL parts.
    """
    if ref_type not in ("branch", "tag"):
        raise HTTPException(404, "unknown ref type")
    base_dir = request.app.state.config.repos_dir
    comp = comparison.compute_comparison(request.app.state.db,
                                          (name, ref_type), base_dir)
    fork_id_to_name = {}
    for s in comp.solutions:
        fork_id_to_name[s.fork_id] = f"{s.owner}/{s.name}"
    ctx = _common(request) | {
        "exercise_name": comp.exercise_name,
        "exercise_type": comp.exercise_type,
        "solutions": comp.solutions,
        "similarities": comp.similarities,
        "fork_name": fork_id_to_name.get,
    }
    return templates.TemplateResponse(request, "comparison.html", ctx)


@router.get("/forks", response_class=HTMLResponse)
def forks_page(request: Request):
    conn = request.app.state.db
    ctx = _common(request) | {
        "root": repos.get_root_repo(conn),
        "forks": repos.list_forks(conn),
    }
    return templates.TemplateResponse(request, "forks.html", ctx)


@router.post("/forks")
def forks_add(request: Request, url: str = Form(...)):
    conn = request.app.state.db
    if repos.get_root_repo(conn) is None:
        raise HTTPException(400, "no root repo configured")
    try:
        repos.add_fork_manual(conn, url)
    except Exception as exc:
        raise HTTPException(400, f"add failed: {exc}")
    return RedirectResponse("/forks", status_code=303)


@router.post("/forks/{fork_id}/sync", response_class=HTMLResponse)
def fork_sync_now(request: Request, fork_id: int):
    conn = request.app.state.db
    fork = next((f for f in repos.list_forks(conn) if f.id == fork_id), None)
    if fork is None:
        raise HTTPException(404, "fork not found")
    jobs.enqueue_analysis(conn, fork_id, kind="sync")
    # When called via HTMX (Hx-Request header) return the partial; else
    # redirect back to the page.
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request, "_forks_table.html",
            {"forks": repos.list_forks(conn)},
        )
    return RedirectResponse("/forks", status_code=303)


@router.get("/admin/aliases", response_class=HTMLResponse)
def admin_aliases_page(request: Request):
    """Manage author identity normalisation: aliases + ignored authors."""
    conn = request.app.state.db
    ctx = _common(request) | {
        "aliases": aliases_mod.list_aliases(conn),
        "ignored": aliases_mod.list_ignored(conn),
        "emails": aliases_mod.distinct_commit_emails(conn),
    }
    return templates.TemplateResponse(request, "admin_aliases.html", ctx)


@router.post("/admin/aliases/add")
def admin_aliases_add(request: Request,
                       alias_email: str = Form(...),
                       canonical_email: str = Form(...),
                       display_name: str = Form("")):
    try:
        aliases_mod.add_alias(request.app.state.db, alias_email,
                              canonical_email, display_name or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse("/admin/aliases", status_code=303)


@router.post("/admin/aliases/delete")
def admin_aliases_delete(request: Request, alias_email: str = Form(...)):
    aliases_mod.remove_alias(request.app.state.db, alias_email)
    return RedirectResponse("/admin/aliases", status_code=303)


@router.post("/admin/aliases/ignore")
def admin_aliases_ignore(request: Request, email: str = Form(...)):
    try:
        aliases_mod.ignore_author(request.app.state.db, email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse("/admin/aliases", status_code=303)


@router.post("/admin/aliases/unignore")
def admin_aliases_unignore(request: Request, email: str = Form(...)):
    aliases_mod.unignore_author(request.app.state.db, email)
    return RedirectResponse("/admin/aliases", status_code=303)


@router.get("/debug/jobs", response_class=HTMLResponse)
def debug_jobs_page(request: Request):
    """Last 100 failed analysis jobs + log file location."""
    ctx = _common(request) | {
        "jobs": jobs.recent_failed_jobs(request.app.state.db, limit=100),
        "log_path": getattr(request.app.state, "log_path", None),
    }
    return templates.TemplateResponse(request, "debug_jobs.html", ctx)


@router.get("/debug/log", response_class=HTMLResponse)
def debug_log_page(request: Request, n: int = 200):
    """Tail the last `n` lines of the app log."""
    log_path: Path | None = getattr(request.app.state, "log_path", None)
    lines: list[str] = []
    if log_path and Path(log_path).exists():
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-max(1, n):]
    ctx = _common(request) | {
        "lines": lines,
        "log_path": log_path,
    }
    return templates.TemplateResponse(request, "debug_log.html", ctx)


def mount_static(app):
    """Mount /static/* — kept as a function so app/main.py can choose to
    skip it in tests that don't care about css."""
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)),
                  name="static")
