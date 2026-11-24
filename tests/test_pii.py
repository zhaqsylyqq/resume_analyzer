"""SEC-02a — personal data is removed locally before resume text goes to the AI provider."""
import re
from datetime import date
from pathlib import Path

import pytest

from app.services import pii
from app.services.parsing import parse_file

from .helpers import create_session, upload

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 17)


def sample_names():
    source = (ROOT / "scripts" / "make_samples.py").read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in re.finditer(r'\(\s*"(\d\d_[a-z_]+)",\s*"\w+",\s*"\w+",\s*\[\s*"([^"]+)"', source)}


def leaked_tokens(name, text):
    """Name tokens still present as words (any case ending) in the redacted text."""
    words = set(re.findall(r"[\w'’-]+", text.lower()))
    return [t for t in name.split() if any(w == t.lower() or (len(t) > 4 and w.startswith(t.lower()[:-1])) for w in words)]


@pytest.mark.parametrize("path", sorted((ROOT / "samples" / "resumes").iterdir()), ids=lambda p: p.stem)
def test_every_sample_resume_loses_the_candidate_name(path):
    name = sample_names()[path.stem]
    parsed = parse_file(path.name, path.read_bytes(), max_bytes=5_000_000, max_chars=20_000)
    result = pii.redact(parsed.text, TODAY)
    assert result.full_name == name
    assert not leaked_tokens(name, result.text)
    assert pii.NAME in result.text


def test_contacts_and_identifiers_are_removed_and_kept_locally():
    text = ("Айгерим Сапарова\nPython-разработчик\n"
            "Тел.: +7 (701) 123-45-67, доп. 8 727 250 00 01. E-mail: aigerim.saparova@mail.kz\n"
            "ИИН 990101400123, паспорт N12345678. LinkedIn: https://linkedin.com/in/asaparova, Telegram @asaparova\n"
            "Адрес: г. Алматы, ул. Абая 10, кв. 5\n"
            "Опыт: Kaspi, 2019 – 2024, Python 3.12, команда из 8 человек, опыт 12 лет, 1С:Бухгалтерия 8.3.")
    result = pii.redact(text, TODAY)
    out = result.text
    for secret in ("701", "250 00 01", "aigerim", "990101400123", "12345678", "linkedin.com", "@asaparova",
                   "Абая", "Сапарова", "Айгерим"):
        assert secret not in out, secret
    assert result.contacts.email == "aigerim.saparova@mail.kz"
    assert result.contacts.phone == "+7 (701) 123-45-67"
    for kept in ("Kaspi", "2019 – 2024", "Python 3.12", "8 человек", "опыт 12 лет", "1С:Бухгалтерия 8.3"):
        assert kept in out, kept
    assert result.removed["phone"] == 2 and result.removed["email"] == 1


def test_demographics_are_extracted_then_removed():
    text = ("Мария Иванова\nПол: женский. Дата рождения: 14.02.1994\nСемейное положение: замужем, двое детей.\n"
            "Национальность: казашка. Фото прилагается.\nОпыт: Senior Python Developer.")
    result = pii.redact(text, TODAY)
    demo = result.demographics
    assert (demo.gender, demo.birth_year, demo.age, demo.marital_status, demo.nationality, demo.photo_mentioned) == (
        "female", 1994, 32, "married", "казашка", True)
    for gone in ("женский", "1994", "замужем", "детей", "казашка", "Фото"):
        assert gone not in result.text, gone
    assert "Senior Python Developer" in result.text


@pytest.mark.parametrize("text, gender, age", [
    ("Андрей Петров\nМужчина, 41 год, женат.", "male", 41),
    ("Мұрат Әбенов\nЖынысы: ер. Жасы: 45", "male", 45),
    ("Александр Ли\nМне 29 лет.", "unknown", 29),
    ("Ольга Волкова\nВ 2020 году я окончила КБТУ.", "female", None),
    ("Олжас Нурланов\nЯ работал в Kaspi.", "male", None),
    ("Viktoria Kim\nGender: female. Age: 26", "female", 26),
])
def test_gender_and_age_variants(text, gender, age):
    demo = pii.redact(text, TODAY).demographics
    assert (demo.gender, demo.age) == (gender, age)


@pytest.mark.parametrize("text, name", [
    ("ФИО: Сейтқали Ерлан Маратұлы\nPython-әзірлеуші", "Сейтқали Ерлан Маратұлы"),
    ("Резюме\nНұрланова Әйгерім Бауыржанқызы\nДеректер талдаушысы", "Нұрланова Әйгерім Бауыржанқызы"),
    ("Александр Ли\nFrontend Developer", "Александр Ли"),
    ("Name: Dmitry Orlov\nLead Python Engineer", "Dmitry Orlov"),
])
def test_name_sources(text, name):
    result = pii.redact(text, TODAY)
    assert result.full_name == name
    assert not leaked_tokens(name, result.text)


def test_inflected_name_later_in_the_text_is_removed():
    text = "Айгерим Сапарова\nО себе: Сапаровой Айгерим доверяли запуск продукта. Рекомендации от Айгерим."
    out = pii.redact(text, TODAY).text
    assert "Сапаров" not in out and "Айгерим" not in out


def test_common_words_sharing_a_name_stem_survive():
    out = pii.redact("Роза Ахметова\nОпыт: розничные продажи, Арман-сервис. Роза вела 3 магазина.", TODAY).text
    assert "розничные продажи" in out and "Роза вела" not in out


def test_short_surname_is_removed_only_as_a_whole_word():
    out = pii.redact("Виктория Ким\nКим руководила командой. Навыки: Kimball, SQL, лишь бы ли.", TODAY).text
    assert "Ким руководила" not in out and "Kimball" in out and "лишь бы ли" in out


def test_referees_with_initials_are_removed_but_named_institutions_stay():
    text = ("Мария Иванова\nОбразование: КазНУ им. аль-Фараби; Л.Н. Гумилев атындағы ЕҰУ.\n"
            "Рекомендации: Петров А.С., руководитель отдела.")
    out = pii.redact(text, TODAY).text
    assert "Петров" not in out and pii.PERSON in out
    assert "им. аль-Фараби" in out and "Л.Н. Гумилев атындағы ЕҰУ" in out


def test_ordinary_resume_text_is_left_alone():
    text = "Data Analyst\nОпыт: 2015-2018, 2018 – н.в. SQL, Python, Power BI. Сертификат AWS 2023. Команда 15 человек."
    result = pii.redact(text, TODAY)
    assert result.text == text and result.removed == {} and result.full_name is None


# The AI request itself ---------------------------------------------------------------------

RESUME = ("Айгерим Сапарова\nПол: женский. Дата рождения: 01.01.1999. Замужем.\n"
          "Тел.: +7 701 123 45 67, aigerim@example.kz, ИИН 990101400123\n"
          "Python-разработчик, Django, PostgreSQL, Docker, 5 лет опыта.")


def test_ai_request_carries_no_personal_data(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("cv.txt", RESUME.encode())])
    sent = "\n".join(str(call["contents"]) + str(call["config"].system_instruction) for call in fake_gemini.client.models.calls)
    for secret in ("Айгерим", "Сапаров", "701 123", "aigerim@", "990101400123", "1999", "женский", "Замужем"):
        assert secret not in sent, secret
    assert "Django" in sent


def test_profile_keeps_name_contacts_and_demographics_locally(app, client, csrf, fake_gemini):
    from app.services import storage

    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("cv.txt", RESUME.encode())])
    with app.app_context():
        db = storage.get_db()
        row = db.execute("SELECT * FROM resumes").fetchone()
        profile = storage.load_profile(row)
        assert profile.full_name == "Айгерим Сапарова"
        assert profile.contacts.email == "aigerim@example.kz" and "701" in profile.contacts.phone
        assert storage.unseal("demographics", "gender", db.execute("SELECT gender FROM demographics").fetchone()[0]) == "female"
