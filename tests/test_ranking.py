import csv
import io
import itertools

import pytest

from app.services import scoring
from app.services.gemini import GeminiService
from app.services.scoring import RankedCandidate, parse_top, to_csv, top_n

from .fakes import PROFILE, FakeClient, smart_handler
from .helpers import VACANCY_TEXT, create_session, resume_text, upload


def candidate(rank, score, **kw):
    base = dict(rank=rank, resume_id=rank, filename=f"{rank}.pdf", name=f"N{rank}", headline=None,
                experience_years=None, email=None, phone=None, score=score, verdict="good", summary="ok")
    return RankedCandidate(**{**base, **kw})


def test_top_n_keeps_ties_at_the_cutoff():
    ranked = [candidate(1, 90), candidate(2, 80), candidate(2, 80), candidate(4, 70)]
    assert [c.score for c in top_n(ranked, 2)] == [90, 80, 80]
    assert len(top_n(ranked, None)) == 4
    assert len(top_n(ranked, 10)) == 4


@pytest.mark.parametrize("raw,expected", [("5", 5), ("all", None), ("", None), (None, None), ("abc", 10), ("0", 10), ("-3", 10)])
def test_parse_top(raw, expected):
    assert parse_top(raw) == expected


def test_csv_has_bom_semicolons_localized_headers_and_no_formulas():
    rows = [candidate(1, 91, name="=HYPERLINK(\"http://evil\")", summary="+cmd", verdict="strong",
                      matched_skills=["Python", "SQL"], email="a@b.kz")]
    data = to_csv(rows, lambda key: {"verdict.strong": "Сильное"}.get(key, key))
    assert data.startswith("﻿".encode("utf-8"))
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert parsed[0][0] == "csv.rank"
    assert parsed[1][1].startswith("'=") and parsed[1][5] == "'+cmd"
    assert parsed[1][4] == "Сильное" and parsed[1][6] == "Python, SQL"


@pytest.fixture()
def scored_session(app, client, csrf):
    scores = itertools.cycle([55, 92, 78, 92, 30, 61])
    app.extensions["gemini"] = GeminiService(
        FakeClient(handler=smart_handler(score_for=lambda _contents: next(scores))), "m", sleep=lambda s: None)
    session_id = create_session(client, csrf, vacancies=[("Python", VACANCY_TEXT), ("Data", VACANCY_TEXT + " SQL")])
    upload(client, csrf, session_id, [(f"cv{i}.txt", resume_text(i)) for i in range(6)])
    return session_id


def vacancy_ids(app, session_id):
    from app.services import storage
    with app.app_context():
        return [v["id"] for v in storage.list_vacancies(storage.get_db(), session_id)]


def test_ranking_is_sorted_with_competition_ranks(app, scored_session):
    from app.services import storage
    vid = vacancy_ids(app, scored_session)[0]
    with app.app_context():
        ranked = scoring.ranking(storage.get_db(), vid)
    assert [c.score for c in ranked] == [92, 92, 78, 61, 55, 30]
    assert [c.rank for c in ranked] == [1, 1, 3, 4, 5, 6]
    assert ranked[0].name == PROFILE["full_name"] and ranked[0].matched_skills == ["Python"]


def test_ranking_page_top_n(app, client, scored_session):
    vid = vacancy_ids(app, scored_session)[0]
    html = client.get(f"/sessions/{scored_session}/vacancies/{vid}?top=5").get_data(as_text=True)
    assert "Показано 5 из 6" in html
    assert html.count('<span class="rank') == 5
    assert "Сильное соответствие" in html
    all_html = client.get(f"/sessions/{scored_session}/vacancies/{vid}?top=all").get_data(as_text=True)
    assert "Показано 6 из 6" in all_html


def test_ranking_page_has_tabs_for_each_vacancy(app, client, scored_session):
    first, second = vacancy_ids(app, scored_session)
    html = client.get(f"/sessions/{scored_session}/vacancies/{first}").get_data(as_text=True)
    assert f"/vacancies/{second}" in html and 'aria-current="page">Python' in html


def test_export_csv(app, client, scored_session):
    vid = vacancy_ids(app, scored_session)[0]
    response = client.get(f"/sessions/{scored_session}/vacancies/{vid}/export.csv?top=2")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    disposition = response.headers["Content-Disposition"]
    assert 'filename="Python_top2.csv"' in disposition and "filename*=UTF-8''" in disposition
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][:4] == ["Место", "Кандидат", "Файл", "Балл"]
    assert len(rows) == 3  # header + two tied 92s
    body = response.data.decode("utf-8-sig")
    for leaked in ("female", "1999", "казашка", "замужем"):
        assert leaked not in body


def test_vacancy_from_another_session_is_404(app, client, csrf, scored_session):
    other = create_session(client, csrf, title="Другая")
    vid = vacancy_ids(app, scored_session)[0]
    assert client.get(f"/sessions/{other}/vacancies/{vid}").status_code == 404
    assert client.get(f"/sessions/{other}/vacancies/{vid}/export.csv").status_code == 404


def test_empty_ranking_shows_empty_state(app, client, csrf):
    session_id = create_session(client, csrf)
    vid = vacancy_ids(app, session_id)[0]
    html = client.get(f"/sessions/{session_id}/vacancies/{vid}").get_data(as_text=True)
    assert "Пока нет оценённых кандидатов" in html
