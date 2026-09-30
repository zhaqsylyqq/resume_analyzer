from pathlib import Path

import pytest

from app.services import bias, storage
from app.services.gemini import GeminiService

from .fakes import FakeClient, api_error, smart_handler
from .helpers import VACANCY_TEXT, create_session

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
BIASED = (SAMPLES / "vacancy_accountant_biased_ru.txt").read_text(encoding="utf-8")


def categories(text):
    return {f.category for f in bias.rule_findings(text)}


def test_sample_biased_vacancy_is_flagged_in_every_category():
    assert {"age", "gender", "appearance", "family", "ethnicity"} <= categories(BIASED)


@pytest.mark.parametrize("name", ["vacancy_python_ru.txt", "vacancy_data_kk.txt"])
def test_clean_sample_vacancies_have_no_findings(name):
    assert bias.rule_findings((SAMPLES / name).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "text,category",
    [
        ("Требуется девушка на ресепшн", "gender"),
        ("Кандидат не старше 35 лет", "age"),
        ("В молодой коллектив", "age"),
        ("Только мужчины", "gender"),
        ("Приятной внешности, рост от 170", "appearance"),
        ("Резюме с фото обязательно", "appearance"),
        ("Без детей, не замужем", "family"),
        ("Славянской внешности", "ethnicity"),
        ("Без вредных привычек", "health"),
        ("35 жасқа дейінгі маман", "age"),
        ("Қыздар шақырылады", "gender"),
        ("Балалары жоқ болуы керек", "family"),
        ("Young and dynamic team, male candidates only", "gender"),
    ],
)
def test_biased_phrases_are_detected(text, category):
    assert category in categories(text)


@pytest.mark.parametrize(
    "text",
    [
        "Приглашаем мужчин и женщин любого возраста",
        "Независимо от пола и возраста",
        "Ер мен әйел үміткерлер",
        "Работа с фото и видео в Photoshop",
        "Опыт работы от 3 лет, английский B2",
        "Қызылорда филиалы, мемлекеттік қызмет",
        "Стройной архитектуры микросервисов",
        "Men and women are welcome",
    ],
)
def test_legitimate_or_inclusive_text_is_not_flagged(text):
    assert bias.rule_findings(text) == []


def ai_service(reply):
    return GeminiService(FakeClient(handler=smart_handler(bias=reply)), "m", sleep=lambda s: None)


def test_ai_findings_are_merged_verified_and_explained():
    reply = {
        "findings": [
            {"phrase": "девушка до 30 лет", "category": "gender", "severity": "high",
             "explanation": "Пол и возраст не связаны с работой.", "suggestion": "Кандидат с опытом от 2 лет"},
            {"phrase": "этой фразы нет в тексте", "category": "other", "severity": "medium",
             "explanation": "x", "suggestion": "y"},
            {"phrase": "дружный", "category": "other", "severity": "medium",
             "explanation": "Размытое требование.", "suggestion": "Опишите команду."},
        ],
        "rewrite": "Требуется бухгалтер с опытом от 2 лет.",
    }
    report = bias.check(BIASED, "ru", ai_service(reply))
    assert report["ai_checked"] is True
    phrases = [f["phrase"] for f in report["findings"]]
    assert "этой фразы нет в тексте" not in phrases
    ai = next(f for f in report["findings"] if f["phrase"] == "девушка до 30 лет")
    assert ai["source"] == "ai" and ai["explanation"].startswith("Пол")
    assert not any(f["source"] == "rule" and f["start"] < ai["end"] and ai["start"] < f["end"] for f in report["findings"])
    assert "дружный" in phrases
    assert report["rewrite"].startswith("Требуется бухгалтер")
    assert report["findings"] == sorted(report["findings"], key=lambda f: f["start"])


def test_ai_failure_falls_back_to_rules():
    service = GeminiService(FakeClient([api_error(400)]), "m", sleep=lambda s: None)
    report = bias.check(BIASED, "ru", service)
    assert report["ai_checked"] is False and report["counts"]["high"] >= 4


def test_highlight_escapes_html():
    text = "<script>alert(1)</script> Нужна девушка"
    html = str(bias.highlight(text, bias.check(text)["findings"]))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<mark class="bias bias--high">девушка</mark>' in html


def test_localize_fills_default_explanations():
    report = bias.localize(bias.check("Нужна девушка"), lambda key: f"T[{key}]")
    finding = report["findings"][0]
    assert finding["label"] == "T[bias.cat.gender]" and finding["explanation"] == "T[bias.why.gender]"


# Routes ---------------------------------------------------------------------------------

def vacancy_id(app, session_id):
    with app.app_context():
        return storage.list_vacancies(storage.get_db(), session_id)[0]["id"]


def test_new_session_stores_rule_report_and_session_page_shows_badge(app, client, csrf):
    session_id = create_session(client, csrf, vacancies=[("Бухгалтер", BIASED)])
    with app.app_context():
        assert storage.list_vacancies(storage.get_db(), session_id)[0]["bias_json"]
    assert "Предвзятые формулировки:" in client.get(f"/sessions/{session_id}").get_data(as_text=True)


def test_ranking_page_shows_findings_and_highlight(app, client, csrf):
    session_id = create_session(client, csrf, vacancies=[("Бухгалтер", BIASED)])
    html = client.get(f"/sessions/{session_id}/vacancies/{vacancy_id(app, session_id)}").get_data(as_text=True)
    assert "Проверка формулировок" in html and '<mark class="bias' in html
    assert "Быстрая проверка по словарю" in html


def test_recheck_with_ai(app, client, csrf):
    app.extensions["gemini"] = ai_service({"findings": [], "rewrite": "Нейтральный текст вакансии."})
    session_id = create_session(client, csrf, vacancies=[("Бухгалтер", BIASED)])
    vid = vacancy_id(app, session_id)
    response = client.post(f"/sessions/{session_id}/vacancies/{vid}/bias", data={"csrf_token": csrf, "top": "5"})
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/vacancies/{vid}?top=5#bias")
    html = client.get(f"/sessions/{session_id}/vacancies/{vid}").get_data(as_text=True)
    assert "Проверено словарём и ИИ" in html and "Нейтральный текст вакансии." in html


def test_recheck_without_api_key_uses_rules(app, client, csrf):
    app.config["GEMINI_API_KEY"] = ""
    session_id = create_session(client, csrf, vacancies=[("Бухгалтер", BIASED)])
    response = client.post(f"/sessions/{session_id}/vacancies/{vacancy_id(app, session_id)}/bias",
                           data={"csrf_token": csrf}, follow_redirects=True)
    assert "ИИ сейчас недоступен" in response.get_data(as_text=True)


def test_bias_check_json_endpoint(app, client, csrf):
    app.extensions["gemini"] = ai_service({"findings": [], "rewrite": ""})
    response = client.post("/vacancies/bias-check", json={"text": BIASED, "language": "kk"}, headers={"X-CSRF-Token": csrf})
    body = response.get_json()
    assert response.status_code == 200 and body["ai_checked"] is True
    assert body["findings"][0]["label"] and body["findings"][0]["explanation"]


def test_bias_check_json_requires_csrf_and_text(client, csrf):
    assert client.post("/vacancies/bias-check", json={"text": BIASED}).status_code == 400
    assert client.post("/vacancies/bias-check", json={"text": "x"}, headers={"X-CSRF-Token": csrf}).status_code == 400


def test_edited_vacancy_text_invalidates_cached_report(app, client, csrf):
    session_id = create_session(client, csrf, vacancies=[("Python", VACANCY_TEXT)])
    vid = vacancy_id(app, session_id)
    with app.app_context():
        db = storage.get_db()
        db.execute("UPDATE vacancies SET text = ? WHERE id = ?", (BIASED, vid))
        db.commit()
    html = client.get(f"/sessions/{session_id}/vacancies/{vid}").get_data(as_text=True)
    assert '<mark class="bias' in html
