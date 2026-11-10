"""CR-01 (SIS 10-11) — candidate pipeline: status + note per candidate and vacancy,
status filter, name search, and status/note in every export."""
import csv
import io

import pytest
from openpyxl import load_workbook

from app.services import storage

from .conftest import login, make_user
from .helpers import VACANCY_TEXT, create_session, resume_text, upload


@pytest.fixture()
def ranked(app, client, csrf, fake_gemini):
    """A session with one vacancy and 4 scored resumes; returns (sid, vid, [resume ids])."""
    sid = create_session(client, csrf)
    upload(client, csrf, sid, [(f"cand{i}.txt", resume_text(i)) for i in range(4)])
    with app.app_context():
        db = storage.get_db()
        vid = storage.list_vacancies(db, sid)[0]["id"]
        rids = [r["id"] for r in db.execute("SELECT id FROM resumes WHERE session_id = ? ORDER BY id", (sid,))]
    return sid, vid, rids


def set_status(client, csrf, sid, vid, rid, status, note="", **extra):
    return client.post(f"/sessions/{sid}/vacancies/{vid}/candidates/{rid}/status",
                       data={"csrf_token": csrf, "status": status, "note": note, **extra})


def page(client, sid, vid, query="top=all"):
    return client.get(f"/sessions/{sid}/vacancies/{vid}?{query}").get_data(as_text=True)


def test_every_candidate_starts_as_new(client, ranked):
    sid, vid, _ = ranked
    html = page(client, sid, vid)
    assert html.count('<option value="new" selected>') == 4
    assert "Все статусы <span>4</span>" in html


def test_set_status_and_note_persist(app, client, csrf, ranked):
    sid, vid, rids = ranked
    response = set_status(client, csrf, sid, vid, rids[1], "interview", "  Интервью в пятницу  ", top="5")
    assert response.status_code == 302 and response.headers["Location"].endswith(f"top=5#c{rids[1]}")
    html = page(client, sid, vid)
    assert '<option value="interview" selected>' in html and 'value="Интервью в пятницу"' in html
    with app.app_context():
        row = storage.get_db().execute("SELECT * FROM candidate_status WHERE resume_id = ?", (rids[1],)).fetchone()
    assert row["status"] == "interview" and row["note"] == "Интервью в пятницу" and row["updated_by"] is not None


def test_json_reply_for_the_no_reload_ui(client, csrf, ranked):
    sid, vid, rids = ranked
    response = client.post(f"/sessions/{sid}/vacancies/{vid}/candidates/{rids[0]}/status",
                           data={"status": "offer"}, headers={"X-CSRF-Token": csrf, "Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json() == {"status": "offer", "label": "Оффер", "message": "Статус кандидата сохранён."}


def test_back_to_new_without_note_removes_the_row(app, client, csrf, ranked):
    sid, vid, rids = ranked
    set_status(client, csrf, sid, vid, rids[0], "shortlist")
    set_status(client, csrf, sid, vid, rids[0], "new")
    with app.app_context():
        assert storage.get_db().execute("SELECT COUNT(*) FROM candidate_status").fetchone()[0] == 0


def test_note_is_limited_to_500_characters(app, client, csrf, ranked):
    sid, vid, rids = ranked
    set_status(client, csrf, sid, vid, rids[0], "shortlist", "x" * 900)
    with app.app_context():
        note = storage.get_db().execute("SELECT note FROM candidate_status").fetchone()[0]
    assert len(note) == storage.MAX_NOTE


@pytest.mark.parametrize("status", ["hired", "", "NEW", "<script>"])
def test_unknown_status_is_rejected(client, csrf, ranked, status):
    sid, vid, rids = ranked
    assert set_status(client, csrf, sid, vid, rids[0], status).status_code == 400


def test_resume_must_belong_to_the_vacancy(client, csrf, ranked):
    sid, vid, rids = ranked
    assert set_status(client, csrf, sid, vid, 9999, "shortlist").status_code == 404
    assert set_status(client, csrf, sid, 9999, rids[0], "shortlist").status_code == 404


def test_other_recruiter_cannot_change_status(app, csrf, ranked):
    sid, vid, rids = ranked
    make_user(app, "intruder")
    other = app.test_client()
    login(other, "intruder")
    token = other.get("/").get_data(as_text=True).split('name="csrf-token" content="')[1].split('"')[0]
    assert set_status(other, token, sid, vid, rids[0], "rejected").status_code == 404


def test_status_change_requires_csrf(client, ranked):
    sid, vid, rids = ranked
    response = client.post(f"/sessions/{sid}/vacancies/{vid}/candidates/{rids[0]}/status", data={"status": "offer"})
    assert response.status_code in (400, 403)


def test_status_filter_and_counts(client, csrf, ranked):
    sid, vid, rids = ranked
    set_status(client, csrf, sid, vid, rids[0], "shortlist")
    set_status(client, csrf, sid, vid, rids[2], "shortlist")
    set_status(client, csrf, sid, vid, rids[3], "rejected")
    html = page(client, sid, vid, "top=all&status=shortlist")
    assert html.count(f'id="c{rids[0]}"') == 1 and html.count(f'id="c{rids[2]}"') == 1
    assert f'id="c{rids[1]}"' not in html and f'id="c{rids[3]}"' not in html
    assert "Шорт-лист <span>2</span>" in html and "Отказ <span>1</span>" in html and "Новый <span>1</span>" in html
    assert "Показано 2 из 4" in html


def test_unknown_filter_value_shows_everyone(client, ranked):
    sid, vid, rids = ranked
    html = page(client, sid, vid, "top=all&status=bogus")
    assert all(f'id="c{rid}"' in html for rid in rids)


def test_search_by_file_name(client, ranked):
    sid, vid, rids = ranked
    html = page(client, sid, vid, "top=all&q=CAND2")
    assert f'id="c{rids[2]}"' in html and f'id="c{rids[1]}"' not in html


def test_empty_filter_result_message(client, ranked):
    sid, vid, _ = ranked
    assert "Нет кандидатов с таким статусом или именем." in page(client, sid, vid, "top=all&status=offer")


def test_exports_include_status_and_note_and_follow_the_filter(client, csrf, ranked):
    sid, vid, rids = ranked
    set_status(client, csrf, sid, vid, rids[1], "offer", "=SUM(A1)")  # formula-like note stays text
    base = f"/sessions/{sid}/vacancies/{vid}"
    rows = list(csv.reader(io.StringIO(client.get(f"{base}/export.csv?top=all&status=offer").data.decode("utf-8-sig")),
                           delimiter=";"))
    assert rows[0][-2:] == ["Статус", "Заметка"] and len(rows) == 2
    assert rows[1][-2:] == ["Оффер", "'=SUM(A1)"]
    xlsx = client.get(f"{base}/export.xlsx?top=all")
    ws = load_workbook(io.BytesIO(xlsx.data)).worksheets[-1]
    header = [c.value for c in ws[1]]
    statuses = [r[header.index("Статус")] for r in ws.iter_rows(min_row=2, values_only=True)]
    assert sorted(statuses) == ["Новый", "Новый", "Новый", "Оффер"]
    assert client.get(f"{base}/export.pdf?top=all&status=offer").data[:4] == b"%PDF"


def test_status_change_is_audited(app, client, csrf, ranked):
    sid, vid, rids = ranked
    set_status(client, csrf, sid, vid, rids[0], "interview")
    with app.app_context():
        rows = [r for r in storage.list_audit(storage.get_db()) if r["action"] == "candidate_status"]
    assert len(rows) == 1 and rows[0]["detail"] == f"vacancy={vid} resume={rids[0]} status=interview"
    assert rows[0]["session_id"] == sid


def test_status_is_per_vacancy_and_removed_with_the_vacancy(app, client, csrf, fake_gemini):
    sid = create_session(client, csrf, vacancies=(("Python", VACANCY_TEXT), ("Data", "Аналитик данных: SQL, Pandas, Power BI, опыт от 2 лет.")))
    upload(client, csrf, sid, [("one.txt", resume_text(1))])
    with app.app_context():
        db = storage.get_db()
        v1, v2 = [v["id"] for v in storage.list_vacancies(db, sid)]
        rid = db.execute("SELECT id FROM resumes").fetchone()[0]
    set_status(client, csrf, sid, v1, rid, "offer")
    assert '<option value="new" selected>' in page(client, sid, v2)
    client.post(f"/sessions/{sid}/vacancies/{v1}/delete", data={"csrf_token": csrf})
    with app.app_context():
        assert storage.get_db().execute("SELECT COUNT(*) FROM candidate_status").fetchone()[0] == 0
