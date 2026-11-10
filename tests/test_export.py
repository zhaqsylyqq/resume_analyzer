"""US7 — Excel and PDF export, plus the resume-language diversity dimension."""
import io
import itertools

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.services import diversity, export, storage
from app.services.gemini import GeminiService
from app.services.scoring import RankedCandidate

from .fakes import FakeClient, smart_handler
from .helpers import VACANCY_TEXT, create_session, resume_text, upload


def candidate(rank, score, **kw):
    base = dict(rank=rank, resume_id=rank, filename=f"{rank}.pdf", name=f"Кандидат {rank}", headline="Разработчик",
                experience_years=3.5, email="a@b.kz", phone=None, score=score, verdict="good", summary="Подходит")
    return RankedCandidate(**{**base, **kw})


def t(key, **kw):
    return key + ("|" + ",".join(f"{k}={v}" for k, v in sorted(kw.items())) if kw else "")


# Unit: Excel ---------------------------------------------------------------------------

def test_xlsx_has_summary_and_one_sheet_per_vacancy():
    sheets = [export.VacancySheet("Python", [candidate(1, 91, verdict="strong"), candidate(2, 70)], 5),
              export.VacancySheet("Python", [candidate(1, 40, verdict="partial")], 1)]
    wb = load_workbook(io.BytesIO(export.to_xlsx("Осенний набор", sheets, t)))
    assert wb.sheetnames == ["xlsx.summary", "Python", "Python (2)"]
    summary = wb["xlsx.summary"]
    assert summary["A1"].value == "Осенний набор"
    assert [c.value for c in summary[5]] == ["Python", 2, 5, 91, 80, 2]
    ws = wb["Python"]
    assert ws["A1"].value == "csv.rank" and ws["B2"].value == "Кандидат 1"
    assert ws["C2"].value == 91 and isinstance(ws["C2"].value, int)  # numbers stay sortable
    assert ws["D2"].value == "verdict.strong" and ws.freeze_panes == "C2"


def test_xlsx_never_evaluates_formulas_and_never_exports_demographics():
    rows = [candidate(1, 80, name="=HYPERLINK(\"http://evil\")", summary="+cmd", concerns=["@SUM(A1)"],
                      strengths=["- опыт с Python"])]
    data = export.to_xlsx("=1+1", [export.VacancySheet("=v", rows, 1)], t)
    wb = load_workbook(io.BytesIO(data))
    ws = wb["=v"]
    # Shown exactly as written, stored as text (data_type "s"), never as a formula.
    assert ws["B2"].value == "=HYPERLINK(\"http://evil\")" and ws["B2"].data_type == "s"
    assert ws["G2"].value == "+cmd" and ws["K2"].value == "@SUM(A1)" and ws["J2"].value == "- опыт с Python"
    assert wb["xlsx.summary"]["A1"].value == "=1+1" and wb["xlsx.summary"]["A1"].data_type == "s"
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert not any("<f>" in z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("xl/worksheets/"))
    header = [c.value for c in ws[1]]
    assert not any(word in str(header) for word in ("gender", "age", "Пол", "Возраст"))


def test_xlsx_strips_control_characters_instead_of_crashing():
    rows = [candidate(1, 80, name="Bad\x01name\x0b", summary="line\x0cbreak")]
    wb = load_workbook(io.BytesIO(export.to_xlsx("s\x02", [export.VacancySheet("v\x03", rows, 1)], t)))
    assert wb.sheetnames == ["xlsx.summary", "v"]
    assert wb["v"]["B2"].value == "Badname" and wb["v"]["G2"].value == "linebreak"


@pytest.mark.parametrize("title,expected", [("a/b:c*?[d]", "a b c   d"), ("x" * 40, "x" * 31), ("", "Sheet")])
def test_sheet_title_is_valid_for_excel(title, expected):
    assert export._sheet_title(title, set()) == expected


# Unit: PDF -----------------------------------------------------------------------------

def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_pdf_contains_cyrillic_and_kazakh_text():
    rows = [candidate(1, 92, name="Әлия Сейтқали", summary="Тәжірибелі әзірлеуші", matched_skills=["Python", "Django"]),
            candidate(2, 64, name="Иван Петров", verdict="partial", concerns=["Нет Docker"])]
    data = export.to_pdf("Осенний набор", "Python-разработчик", rows, 10, 5, t)
    assert data.startswith(b"%PDF")
    text = pdf_text(data)
    for expected in ("Әлия Сейтқали", "Иван Петров", "Тәжірибелі", "Django", "Нет Docker"):
        assert expected in text


def test_pdf_survives_many_candidates_and_empty_list():
    rows = [candidate(i, 100 - i, summary="Длинное пояснение. " * 20) for i in range(1, 41)]
    assert len(PdfReader(io.BytesIO(export.to_pdf("s", "v", rows, 40, None, t))).pages) > 2
    assert "rank.empty" in pdf_text(export.to_pdf("s", "v", [], 0, 10, t))


# Routes --------------------------------------------------------------------------------

@pytest.fixture()
def scored_session(app, client, csrf):
    scores = itertools.cycle([55, 92, 78, 30, 61, 88])
    app.extensions["gemini"] = GeminiService(
        FakeClient(handler=smart_handler(score_for=lambda _c: next(scores))), "m", sleep=lambda s: None)
    session_id = create_session(client, csrf, vacancies=[("Python", VACANCY_TEXT), ("Data", VACANCY_TEXT + " SQL")])
    upload(client, csrf, session_id, [(f"cv{i}.txt", resume_text(i)) for i in range(6)])
    with app.app_context():
        vids = [v["id"] for v in storage.list_vacancies(storage.get_db(), session_id)]
    return session_id, vids


def test_vacancy_xlsx_export_respects_top_n(client, scored_session):
    sid, vids = scored_session
    response = client.get(f"/sessions/{sid}/vacancies/{vids[0]}/export.xlsx?top=5")
    assert response.status_code == 200
    assert response.mimetype == export_mimetype("xlsx")
    assert "filename*=UTF-8''" in response.headers["Content-Disposition"] and ".xlsx" in response.headers["Content-Disposition"]
    ws = load_workbook(io.BytesIO(response.data))["Python"]
    assert ws.max_row == 6  # header + top-5
    assert [ws.cell(r, 3).value for r in range(2, 7)] == sorted([ws.cell(r, 3).value for r in range(2, 7)], reverse=True)


def test_vacancy_pdf_export(client, scored_session):
    sid, vids = scored_session
    response = client.get(f"/sessions/{sid}/vacancies/{vids[1]}/export.pdf?top=all")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    text = pdf_text(response.data)
    assert "Шорт-лист: Data" in text and "6 из 6" in text


def test_session_xlsx_has_every_vacancy(client, scored_session):
    sid, _ = scored_session
    response = client.get(f"/sessions/{sid}/export.xlsx")
    wb = load_workbook(io.BytesIO(response.data))
    assert wb.sheetnames == ["Сводка", "Python", "Data"]
    assert wb["Сводка"]["A1"].value == "Осенний набор"


def test_exports_404_for_foreign_vacancy(client, scored_session, app, csrf):
    sid, vids = scored_session
    other = create_session(client, csrf, title="Другая")
    for ext in ("xlsx", "pdf", "csv"):
        assert client.get(f"/sessions/{other}/vacancies/{vids[0]}/export.{ext}").status_code == 404
    assert client.get("/sessions/999/export.xlsx").status_code == 404


def test_ranking_page_shows_all_export_buttons(client, scored_session):
    sid, vids = scored_session
    html = client.get(f"/sessions/{sid}/vacancies/{vids[0]}?top=5").get_data(as_text=True)
    for ext in ("xlsx", "pdf", "csv"):
        assert f"/export.{ext}?top=5" in html
    assert f"/sessions/{sid}/export.xlsx" in client.get(f"/sessions/{sid}").get_data(as_text=True)


def test_exports_make_no_ai_calls(app, client, scored_session):
    sid, vids = scored_session
    calls = len(app.extensions["gemini"].client.models.calls)
    for url in (f"/sessions/{sid}/vacancies/{vids[0]}/export.xlsx", f"/sessions/{sid}/vacancies/{vids[0]}/export.pdf",
                f"/sessions/{sid}/export.xlsx", f"/sessions/{sid}/vacancies/{vids[0]}"):
        assert client.get(url).status_code == 200
    assert len(app.extensions["gemini"].client.models.calls) == calls


def export_mimetype(ext):
    from app.routes.vacancies import MIMETYPES
    return MIMETYPES[ext]


# Diversity: resume language -------------------------------------------------------------

def test_diversity_reports_resume_language(app):
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        ids = []
        for i, lang in enumerate(["ru"] * 6 + ["kk"] * 6):
            rid = storage.add_resume(db, sid, f"{i}.txt", f"h{i}", "t", storage.DONE)
            db.execute("UPDATE resumes SET profile_json = ? WHERE id = ?", (f'{{"resume_language": "{lang}"}}', rid))
            ids.append(rid)
        db.commit()
        report = diversity.report(db, ids, ids[:5] + ids[6:7])
    kk = next(g for g in report["dimensions"]["language"]["groups"] if g["key"] == "kk")
    assert kk["pool"] == 6 and kk["flagged"] and kk["ratio"] == 0.2
    assert {"dimension": "language", "group": "kk", "ratio": 0.2} in report["flags"]
    # No demographics rows at all: gender and age are simply unknown.
    assert report["dimensions"]["gender"]["known_share"] == 0
