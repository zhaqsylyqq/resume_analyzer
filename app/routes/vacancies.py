"""Vacancies inside a session: add one later (F6) and view/export its ranking (F2)."""
from __future__ import annotations

import re
from urllib.parse import quote

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from ..i18n import translate
from ..services import jobs, scoring, storage
from .screening import start_analysis
from .sessions import MAX_VACANCIES, load_session_or_404, validate_vacancy

bp = Blueprint("vacancies", __name__, url_prefix="/sessions")


def _load(session_id: int, vacancy_id: int):
    session = load_session_or_404(session_id)
    vacancy = storage.get_vacancy(storage.get_db(), session_id, vacancy_id)
    if vacancy is None:
        abort(404)
    return session, vacancy


@bp.post("/<int:session_id>/vacancies")
def create(session_id: int):
    load_session_or_404(session_id)
    db = storage.get_db()
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()

    if len(storage.list_vacancies(db, session_id)) >= MAX_VACANCIES:
        flash("form.error.too_many_vacancies", "danger")
    elif error := validate_vacancy(title, text):
        flash(error, "danger")
    else:
        storage.add_vacancy(db, session_id, title, text)
        if storage.requeue_unscored(db, session_id):
            start_analysis(session_id)
        flash("vacancy.added", "success")
    return redirect(url_for("sessions.show", session_id=session_id))


@bp.get("/<int:session_id>/vacancies/<int:vacancy_id>")
def show(session_id: int, vacancy_id: int):
    session, vacancy = _load(session_id, vacancy_id)
    db = storage.get_db()
    top = scoring.parse_top(request.args.get("top", str(scoring.TOP_CHOICES[1])))
    candidates = scoring.ranking(db, vacancy_id)
    progress = storage.progress(db, session_id)
    return render_template(
        "vacancies/show.html",
        session=session,
        vacancy=vacancy,
        vacancies=storage.list_vacancies(db, session_id),
        candidates=scoring.top_n(candidates, top),
        total=len(candidates),
        stats=scoring.summary_stats(candidates),
        top=top,
        top_choices=scoring.TOP_CHOICES,
        in_progress=jobs.is_running(session_id) or progress[storage.PENDING] > 0,
    )


def _filename(*parts: str) -> str:
    return "_".join(re.sub(r"[^\w\-]+", "-", p, flags=re.UNICODE).strip("-")[:60] for p in parts if p) + ".csv"


@bp.get("/<int:session_id>/vacancies/<int:vacancy_id>/export.csv")
def export(session_id: int, vacancy_id: int):
    session, vacancy = _load(session_id, vacancy_id)
    top = scoring.parse_top(request.args.get("top"))
    candidates = scoring.top_n(scoring.ranking(storage.get_db(), vacancy_id), top)
    name = _filename(session["title"], vacancy["title"], f"top{top}" if top else "all")
    ascii_stem = re.sub(r"[-_]{2,}", "_", name[:-4].encode("ascii", "ignore").decode()).strip("_-")
    ascii_name = f"{ascii_stem or 'ranking'}.csv"
    response = Response(scoring.to_csv(candidates, translate), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    response.headers["Cache-Control"] = "no-store"
    return response
