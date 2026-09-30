import pytest

from app.services.anonymize import REDACTED, scoring_view, scrub_text
from app.services.schemas import CandidateProfile

from .fakes import PROFILE


@pytest.mark.parametrize(
    "text",
    [
        "Дата рождения: 12.03.1995",
        "Возраст: 29",
        "Мне 29 лет, ищу работу",
        "Пол: женский",
        "Жынысы: ер",
        "Семейное положение: женат",
        "Замужем, есть 2 детей",
        "Отбасылық жағдайы: үйленген",
        "Национальность: русский",
        "Ұлты: қазақ",
        "Born 1990 in Almaty",
        "I am 31 years old",
        "Female, married",
        "Фото прилагается",
    ],
)
def test_demographic_mentions_are_removed(text):
    cleaned = scrub_text(text)
    assert REDACTED in cleaned
    for word in ("1995", "29", "женский", "ер", "женат", "Замужем", "үйленген", "русский", "қазақ",
                 "1990", "31", "Female", "married", "Фото"):
        if word in text:
            assert word not in cleaned.replace(REDACTED, ""), (text, cleaned)


@pytest.mark.parametrize(
    "text",
    [
        "10 лет опыта в продажах",
        "Руководил командой из 12 человек",
        "Опыт работы с детьми в школе",
        "Фотограф, ретушь в Photoshop",
        "Single-page applications на React",
    ],
)
def test_job_relevant_text_is_kept(text):
    assert scrub_text(text) == text


def test_contacts_are_removed():
    cleaned = scrub_text("Пишите: a.b@mail.kz, +7 (701) 123-45-67, https://linkedin.com/in/x")
    assert "mail.kz" not in cleaned and "123-45-67" not in cleaned and "linkedin" not in cleaned


def test_scoring_view_drops_identity_and_demographics():
    view = scoring_view(CandidateProfile.model_validate(PROFILE))
    assert set(view) == {"headline", "summary", "total_experience_years", "skills", "experience",
                         "education", "languages", "certifications"}
    assert "Айгерим" not in view["summary"]
    assert "Замужем" not in view["summary"]
    assert view["experience"][0] == {"title": "Backend Developer", "organization": "Kaspi",
                                     "duration_years": 3, "highlights": ["Разработала API платежей"]}
