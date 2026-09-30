"""Vacancies inside a session: add one later (F6 — scores only the missing pairs)."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, request, url_for

from ..services import storage
from .screening import start_analysis
from .sessions import MAX_VACANCIES, load_session_or_404, validate_vacancy

bp = Blueprint("vacancies", __name__, url_prefix="/sessions")


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
