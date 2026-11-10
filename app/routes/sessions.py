"""F3 — screening sessions: create, list, open, status polling, delete (scoped by owner, A1)."""
from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from ..auth import can_access, owner_filter

from ..i18n import get_locale, translate
from ..services import bias, jobs, storage

bp = Blueprint("sessions", __name__, url_prefix="/sessions")

MAX_VACANCIES = 10
MAX_TITLE = 200
MIN_VACANCY_TEXT = 30
MAX_VACANCY_TEXT = 20_000


def load_session_or_404(session_id: int):
    """The session if the signed-in user may see it; 404 otherwise (never 403: no hints)."""
    session = storage.get_session(storage.get_db(), session_id)
    if session is None or not can_access(session):
        abort(404)
    return session


def validate_vacancy(title: str, text: str) -> str | None:
    """Return an i18n error key, or None if valid."""
    if not title or len(title) > MAX_TITLE:
        return "form.error.vacancy_title"
    if len(text) < MIN_VACANCY_TEXT or len(text) > MAX_VACANCY_TEXT:
        return "form.error.vacancy_text"
    return None


def resume_json(row) -> dict[str, Any]:
    profile = json.loads(row["profile_json"]) if row["profile_json"] else {}
    status = row["status"]
    error = row["error"]
    if status == storage.PARSE_FAILED:
        message = translate(f"parse_error.{error}")
    elif status == storage.ANALYSIS_FAILED:
        message = translate(f"analysis_error.{error or 'internal'}")
    else:
        message = None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "status": status,
        "status_label": translate(f"status.{status}"),
        "message": message,
        "name": profile.get("full_name"),
        "headline": profile.get("headline"),
    }


@bp.get("")
def index():
    return render_template("sessions/list.html", sessions=storage.list_sessions(storage.get_db(), owner_id=owner_filter()))


@bp.get("/new")
def new():
    return render_template("sessions/new.html", form={"title": "", "language": get_locale(), "vacancies": [("", "")]}, errors={})


@bp.post("")
def create():
    title = request.form.get("title", "").strip()
    language = request.form.get("language", get_locale())
    titles = [t.strip() for t in request.form.getlist("vacancy_title")]
    texts = [t.strip() for t in request.form.getlist("vacancy_text")]
    vacancies = [(t, x) for t, x in zip(titles, texts) if t or x]

    errors: dict[str, Any] = {}
    if not title or len(title) > MAX_TITLE:
        errors["title"] = "form.error.title"
    if language not in current_app.config["LANGUAGES"]:
        errors["language"] = "form.error.language"
    if not vacancies:
        errors["vacancies"] = "form.error.no_vacancies"
    elif len(vacancies) > MAX_VACANCIES:
        errors["vacancies"] = "form.error.too_many_vacancies"
    vacancy_errors = {i: e for i, (t, x) in enumerate(vacancies) if (e := validate_vacancy(t, x))}
    if vacancy_errors:
        errors["vacancy"] = vacancy_errors

    if errors:
        form = {"title": title, "language": language, "vacancies": vacancies or [("", "")]}
        return render_template("sessions/new.html", form=form, errors=errors), 400

    db = storage.get_db()
    session_id = storage.create_session(db, title, language, vacancies, owner_id=g.user["id"])
    storage.audit(db, "session_create", g.user, session_id, title)
    for vacancy in storage.list_vacancies(db, session_id):
        storage.save_bias_report(db, vacancy["id"], bias.check(vacancy["text"]))
    return redirect(url_for("sessions.show", session_id=session_id))


@bp.get("/<int:session_id>")
def show(session_id: int):
    session = load_session_or_404(session_id)
    db = storage.get_db()
    storage.audit(db, "session_open", g.user, session_id)
    vacancies = storage.list_vacancies(db, session_id)
    bias_counts = {v["id"]: json.loads(v["bias_json"])["counts"] for v in vacancies if v["bias_json"]}
    return render_template(
        "sessions/show.html",
        session=session,
        vacancies=vacancies,
        bias_counts=bias_counts,
        resumes=[resume_json(r) for r in storage.list_resumes(db, session_id)],
        progress=storage.progress(db, session_id),
        running=jobs.is_running(session_id),
        eta=_eta(db, session_id),
        remaining_label=_remaining_label(),
    )


def _remaining_label():
    from .screening import remaining_label, remaining_today  # avoid a circular import

    return remaining_label(remaining_today())


def _eta(db, session_id: int) -> dict[str, Any] | None:
    """Q1: time remaining as seconds plus a localized label; None when idle."""
    if not jobs.is_running(session_id):
        return None
    seconds = jobs.eta_seconds(db, session_id, current_app.config["GEMINI_CONCURRENCY"])
    if seconds is None:
        return {"seconds": None, "label": translate("eta.estimating")}
    minutes = -(-seconds // 60)  # round up
    label = translate("eta.under_minute") if seconds < 60 else translate("eta.minutes", minutes=minutes)
    return {"seconds": seconds, "label": label}


@bp.get("/<int:session_id>/status")
def status(session_id: int):
    load_session_or_404(session_id)
    db = storage.get_db()
    eta = _eta(db, session_id)
    return jsonify(
        progress=storage.progress(db, session_id),
        running=jobs.is_running(session_id),
        eta_seconds=eta["seconds"] if eta else None,
        eta_label=eta["label"] if eta else None,
        resumes=[resume_json(r) for r in storage.list_resumes(db, session_id)],
    )


@bp.post("/<int:session_id>/delete")
def delete(session_id: int):
    session = load_session_or_404(session_id)
    db = storage.get_db()
    storage.delete_session(db, session_id)
    storage.audit(db, "session_delete", g.user, session_id, session["title"])
    return redirect(url_for("sessions.index"))
