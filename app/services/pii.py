"""SEC-02a — remove personal data from a resume before any of it leaves the server.

The external AI provider receives only the redacted text. Everything personal that the app
itself needs is extracted here, locally, with deterministic rules:

- name and contacts (shown to the recruiter in the ranking and exports);
- demographics (gender, age, marital status, nationality, photo) for the aggregate B2
  diversity report — never for scoring.

What is removed: the candidate's name (every occurrence, also inflected), e-mails, phone
numbers, IIN and document numbers, links and messenger handles, birth dates and age,
gender, marital status and children, nationality, street addresses, and third-party names
written with initials (referees). Each is replaced by a bracketed marker such as
``[EMAIL]`` so the model sees that something was there.

Limits (residual risk, see docs/specs/SEC-02a-redaction.md): names are found from labels
("ФИО:"), from the resume header and from Russian/Kazakh surname and patronymic endings.
A name written in an unusual place in an unusual form can survive; the scoring step keeps
its own second layer of scrubbing (anonymize.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .schemas import Contacts, Demographics

# Letters of the Russian, Kazakh and Latin alphabets.
UP = "A-ZА-ЯЁӘҒҚҢӨҰҮҺІ"
LOW = "a-zа-яёәғқңөұүһі"
_F = re.IGNORECASE | re.UNICODE

NAME = "[КАНДИДАТ]"
PERSON = "[ИМЯ]"
EMAIL = "[EMAIL]"
PHONE = "[ТЕЛЕФОН]"
ID = "[ДОКУМЕНТ]"
LINK = "[ССЫЛКА]"
BIRTH = "[ДАТА РОЖДЕНИЯ]"
AGE = "[ВОЗРАСТ]"
GENDER = "[ПОЛ]"
FAMILY = "[СЕМЕЙНОЕ ПОЛОЖЕНИЕ]"
NATIONALITY = "[НАЦИОНАЛЬНОСТЬ]"
ADDRESS = "[АДРЕС]"
PHOTO = "[ФОТО]"


@dataclass
class Redaction:
    text: str
    full_name: str | None = None
    contacts: Contacts = field(default_factory=Contacts)
    demographics: Demographics = field(default_factory=Demographics)
    removed: dict[str, int] = field(default_factory=dict)

    def count(self, kind: str, n: int = 1) -> None:
        if n:
            self.removed[kind] = self.removed.get(kind, 0) + n


# Contacts and identifiers -------------------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", _F)
URL_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b(?:linkedin\.com|github\.com|gitlab\.com|t\.me|wa\.me|instagram\.com|facebook\.com|vk\.com|hh\.kz|hh\.ru)/\S+",
    _F,
)
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
IIN_RE = re.compile(r"\b(?:ИИН|ЖСН|IIN|БИН)\s*[:№]?\s*\d{12}\b|(?<!\d)\d{12}(?!\d)", _F)
DOC_RE = re.compile(
    r"\b(?:паспорт|удостоверени[ея](?:\s+личности)?|уд\.\s*личности|жеке\s+куәлік|passport|id\s*card)"
    r"\s*(?:№|N|No\.?|:)?\s*[A-ZА-Я]{0,2}\s?\d{6,9}\b",
    _F,
)
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s\-()]{8,18}\d(?![\d])")


def _is_phone(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    if not 10 <= len(digits) <= 13:
        return False
    # "2015 2018 2021" style lists of years are not phones
    return not re.fullmatch(r"(?:(?:19|20)\d\d[\s\-]*){2,}", candidate.strip())


# Birth date, age, gender, family, nationality, address -------------------------------------

LABEL_END = r"(?=[.;,\n]|$)"
BIRTH_RE = re.compile(
    r"\b(?:дата\s+рождения|год\s+рождения|д\.\s*р\.|родил(?:ся|ась)|туған\s+(?:күні|жылы)|"
    r"date\s+of\s+birth|d\.o\.b\.?|born)\s*[:\-–]?\s*((?:\d{1,2}[./\-]\d{1,2}[./\-])?(?:19|20)\d\d|\d{1,2}\s+\w+\s+(?:19|20)\d\d)(?:[ \t]*(?:г\.|года?|ж\.)(?!\w))?",
    _F,
)
AGE_LABEL_RE = re.compile(r"\b(?:возраст|жасы|жасым|age)\s*[:\-–]?\s*(\d{2})\b(?:\s*(?:лет|года?|жас|years?(?:\s+old)?))?", _F)
AGE_PHRASE_RE = re.compile(
    r"\b(?:мне|i\s+am|i'm)\s+(\d{2})\s*(?:лет|года?|years?(?:\s+old)?)?\b"
    r"|\b(\d{2})\s*(?:years?\s+old|жастамын|жаста)\b",
    _F,
)
GENDER_WORDS = {
    "женский": "female", "женщина": "female", "female": "female", "woman": "female", "әйел": "female",
    "мужской": "male", "мужчина": "male", "male": "male", "man": "male", "ер": "male", "ер адам": "male",
}
GENDER_LABEL_RE = re.compile(r"\b(?:пол|жынысы|gender|sex)\s*[:\-–]\s*(женский|мужской|female|male|әйел|ер)\b", _F)
GENDER_WORD_RE = re.compile(r"(?<![\w\-])(мужчина|женщина)(?:\s*,\s*(\d{2})\s*(?:год|года|лет))?(?![\w\-])", _F)
FIRST_PERSON_RE = re.compile(r"\bя\s+(?:не\s+)?([а-яё]{3,}л(а)?)\b", _F)
FAMILY_LABEL_RE = re.compile(r"\b(?:семейное\s+положение|отбасылық\s+жағдайы|marital\s+status)\s*[:\-–]\s*([^.;,\n]+)", _F)
FAMILY_WORD_RE = re.compile(
    r"(?<![\w\-])(не\s+замужем|не\s+женат|замужем|женат|холост(?:ая|ой)?|разведен(?:а)?|вдов(?:ец|а)|"
    r"married|single|divorced|widowed|үйленген|үйленбеген|тұрмыс\s+құрған|тұрмыс\s+құрмаған)(?![\w\-])",
    _F,
)
CHILDREN_RE = re.compile(
    r"(?:,\s*)?(?:\b(?:есть|имею)\s+)?\b(?:\d+|один|одна|двое|трое|четверо|один\s+ребенок|"
    r"один\s+ребёнок)\s+(?:детей|ребенка|ребёнка|ребенок|ребёнок|детьми)\b|\bбалалары?\s+бар\b|\b\d\s+children\b",
    _F,
)
NATIONALITY_RE = re.compile(r"\b(?:национальность|ұлты|nationality|ethnicity)\s*[:\-–]\s*([^.;,\n]+)", _F)
ADDRESS_LABEL_RE = re.compile(r"\b(?:адрес(?:\s+проживания)?|мекенжай(?:ы)?|address)\s*[:\-–]\s*[^\n]+", _F)
STREET_RE = re.compile(
    r"\b(?:ул\.|улица|пр\.|пр-т|просп\.|проспект|мкр\.?|микрорайон|б-р|бульвар|көшесі|даңғылы|ш\.а\.)"
    r"\s*[^\n,;]{1,40}?\s*,?\s*(?:д\.|дом|үй)?\s*\d+[а-яa-z]?(?:\s*,?\s*(?:кв\.?|квартира|пәтер)\s*\d+)?",
    _F,
)
PHOTO_RE = re.compile(r"\b(?:фото(?:графия)?|сурет|photo)\b", _F)

MARITAL_MAP = [
    (re.compile(r"не\s+замужем|не\s+женат|холост|single|үйленбеген|тұрмыс\s+құрмаған", _F), "single"),
    (re.compile(r"разведен|divorced", _F), "divorced"),
    (re.compile(r"вдов|widowed", _F), "widowed"),
    (re.compile(r"замужем|женат|married|үйленген|тұрмыс\s+құрған", _F), "married"),
]

# Names -------------------------------------------------------------------------------------

NAME_LABEL_RE = re.compile(
    rf"^[ \t]*(?:ФИО|Ф\.\s?И\.\s?О\.|Имя(?:\s+и\s+фамилия)?|Фамилия,?\s+имя(?:,?\s+отчество)?|Аты[\s\-]жөні|"
    rf"Full\s+name|Name)[ \t]*[:\-–][ \t]*([{UP}][{LOW}'’\-]+(?:[ \t]+[{UP}][{LOW}'’\-]+){{0,3}})",
    re.MULTILINE | re.UNICODE,
)
WORD = rf"[{UP}][{LOW}'’\-]+"
NAME_LINE_RE = re.compile(rf"^[ \t]*({WORD}(?:[ \t]+{WORD}){{1,2}})[ \t]*$", re.UNICODE)
SURNAME_RE = re.compile(
    r"(?:ов|ова|ев|ева|ёв|ёва|ин|ина|ын|ына|ский|ская|цкий|цкая|ко|енко|ук|юк|ян|дзе|швили|"
    r"улы|ұлы|кызы|қызы|бекова|баев|баева|uly|kyzy|ov|ova|ev|eva|in|ina|enko|sky|skaya)$",
    re.IGNORECASE,
)
PATRONYMIC_RE = re.compile(r"(?:вич|вна|ична|ұлы|улы|қызы|кызы)$", re.IGNORECASE)
INITIALS_RE = re.compile(rf"\b{WORD}[ \t]+[{UP}]\.[ \t]?[{UP}]\.(?!\w)|\b[{UP}]\.[ \t]?[{UP}]\.[ \t]?{WORD}", re.UNICODE)
NOT_NAME = {
    w.lower() for w in (
        "Резюме", "Curriculum", "Vitae", "CV", "Resume", "Опыт", "Работы", "Образование", "Навыки", "Контакты",
        "Языки", "Цель", "Обо", "Мне", "Профиль", "Summary", "Experience", "Education", "Skills", "Contacts",
        "Senior", "Junior", "Middle", "Lead", "Chief", "Head", "Principal", "Developer", "Engineer", "Analyst",
        "Manager", "Designer", "Architect", "Specialist", "Consultant", "Partner", "Business", "Project",
        "Product", "Data", "Python", "Java", "Frontend", "Backend", "Fullstack", "DevOps", "Software", "Sales",
        "Marketing", "Accountant", "Recruiter", "Менеджер", "Разработчик", "Бухгалтер", "Аналитик",
        "Инженер", "Специалист", "Руководитель", "Директор", "Главный", "Ведущий", "Старший", "Младший",
        "Бизнес", "Продаж", "Тимлид", "Әзірлеуші", "Инженері", "Талдаушы", "Талдаушысы", "Деректер", "Бас",
        "Кіші", "Тәжірибе", "Тәжірибесі", "Білімі", "Дағдылар", "Тілдер", "Жұмыс", "Алматы", "Астана",
        "Шымкент", "Караганда", "Қарағанды", "Казахстан", "Қазақстан", "Республика", "Ltd", "LLP", "Inc",
    )
}


def _looks_like_name(words: list[str], first_line: bool) -> bool:
    lowered = [w.lower() for w in words]
    if any(w in NOT_NAME for w in lowered):
        return False
    if any(SURNAME_RE.search(w) or PATRONYMIC_RE.search(w) for w in words):
        return True
    return first_line  # "Виктория Ким": the first line of a resume is almost always the name


def find_name(text: str) -> str | None:
    match = NAME_LABEL_RE.search(text)
    if match:
        return match.group(1).strip()
    lines = [line for line in text.splitlines() if line.strip()][:4]
    for index, line in enumerate(lines):
        line_match = NAME_LINE_RE.match(line)
        if line_match:
            words = line_match.group(1).split()
            if _looks_like_name(words, first_line=index == 0):
                return " ".join(words)
    return None


def _stem(token: str) -> str:
    """Drop a case ending so that "Сапаровой" and "Айгерим" in any case still match."""
    if len(token) <= 4:
        return token
    return re.sub(r"(?:ой|ей|ою|ую|ая|ий|ым|ом|ем|ов|ев|ин|ын|а|я|у|ю|е|ы|и|о)$", "", token, flags=re.IGNORECASE)


def _name_pattern(full_name: str) -> re.Pattern[str] | None:
    tokens = [t for t in re.split(r"[\s\-]+", full_name) if len(t) >= 2]
    if not tokens:
        return None
    # Long tokens: a capital first letter, then any case and any case ending ("Сапаровой",
    # "САПАРОВА"); the capital keeps ordinary words such as "розница" (Роза) untouched.
    # Short surnames (Ли, Ким, Пак): exact word only.
    def part(token: str) -> str:
        if len(token) < 4:
            return re.escape(token)
        stem = _stem(token)
        return rf"{re.escape(stem[0].upper())}(?i:{re.escape(stem[1:])}[{LOW}]{{0,4}})"

    parts = [part(t) for t in sorted(tokens, key=len, reverse=True)]
    return re.compile(rf"(?<![{UP}{LOW}])(?:{'|'.join(parts)})(?![{UP}{LOW}])", re.UNICODE)


# Main entry point --------------------------------------------------------------------------

def _sub(result: Redaction, pattern: re.Pattern[str], marker: str, kind: str, text: str) -> str:
    text, n = pattern.subn(marker, text)
    result.count(kind, n)
    return text


def _birth_year(value: str) -> int | None:
    match = re.search(r"(19|20)\d\d", value)
    return int(match.group(0)) if match else None


def redact(text: str, today: date | None = None) -> Redaction:
    """Return the text with personal data replaced by markers, plus what was extracted."""
    today = today or date.today()
    result = Redaction(text="")
    demo = result.demographics

    # 1. Contacts and identifiers (before names: an e-mail often contains the name).
    emails = EMAIL_RE.findall(text)
    if emails:
        result.contacts.email = emails[0]
    text = _sub(result, EMAIL_RE, EMAIL, "email", text)
    text = _sub(result, URL_RE, LINK, "link", text)
    text = _sub(result, HANDLE_RE, LINK, "link", text)
    text = _sub(result, IIN_RE, ID, "document", text)
    text = _sub(result, DOC_RE, ID, "document", text)

    def phone(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if not _is_phone(candidate):
            return candidate
        if result.contacts.phone is None:
            result.contacts.phone = candidate.strip()
        result.count("phone")
        return PHONE

    text = PHONE_CANDIDATE_RE.sub(phone, text)

    # 2. The candidate's name, found before anything else changes the header lines.
    name = find_name(text)
    if name:
        result.full_name = name
        pattern = _name_pattern(name)
        if pattern is not None:
            text = _sub(result, pattern, NAME, "name", text)

    def initials(match: re.Match[str]) -> str:
        before = text[max(0, match.start() - 8):match.start()].lower()
        after = text[match.end():match.end() + 12].lower()
        if re.search(r"(?:им\.|имени)\s*$", before) or after.lstrip().startswith(("атындағы", "атында")):
            return match.group(0)  # an institution named after someone: "КазНУ им. ...", "Л.Н. Гумилев атындағы ЕҰУ"
        result.count("third_party_name")
        return PERSON

    text = INITIALS_RE.sub(initials, text)

    # 3. Demographics: remember, then remove.
    for match in BIRTH_RE.finditer(text):
        year = _birth_year(match.group(1))
        if year and demo.birth_year is None and 1920 <= year <= 2020:
            demo.birth_year = year
    text = _sub(result, BIRTH_RE, BIRTH, "birth_date", text)

    def age(match: re.Match[str]) -> str:
        value = next((g for g in match.groups() if g), None)
        if value and 14 <= int(value) <= 100 and demo.age is None:
            demo.age = int(value)
        result.count("age")
        return AGE

    text = AGE_LABEL_RE.sub(age, text)
    text = AGE_PHRASE_RE.sub(age, text)

    def gender_label(match: re.Match[str]) -> str:
        if demo.gender == "unknown":
            demo.gender = GENDER_WORDS[match.group(1).lower()]  # type: ignore[assignment]
        result.count("gender")
        return GENDER

    text = GENDER_LABEL_RE.sub(gender_label, text)

    def gender_word(match: re.Match[str]) -> str:
        if demo.gender == "unknown":
            demo.gender = GENDER_WORDS[match.group(1).lower()]  # type: ignore[assignment]
        result.count("gender")
        if match.group(2):
            if demo.age is None and 14 <= int(match.group(2)) <= 100:
                demo.age = int(match.group(2))
            result.count("age")
            return f"{GENDER}, {AGE}"
        return GENDER

    text = GENDER_WORD_RE.sub(gender_word, text)
    if demo.gender == "unknown":
        # First-person Russian past tense is grammatically gendered: "я работала" / "я работал".
        for match in FIRST_PERSON_RE.finditer(text):
            demo.gender = "female" if match.group(2) else "male"
            break

    def family(match: re.Match[str]) -> str:
        value = match.group(1)
        if demo.marital_status is None:
            demo.marital_status = next((code for rx, code in MARITAL_MAP if rx.search(value)), value.strip()[:40])
        result.count("family")
        return FAMILY

    text = FAMILY_LABEL_RE.sub(lambda m: f"{m.group(0)[: m.start(1) - m.start(0)]}{family(m)}", text)
    text = FAMILY_WORD_RE.sub(family, text)
    text = _sub(result, CHILDREN_RE, "", "family", text)

    def nationality(match: re.Match[str]) -> str:
        if demo.nationality is None:
            demo.nationality = match.group(1).strip()[:40]
        result.count("nationality")
        return NATIONALITY

    text = NATIONALITY_RE.sub(nationality, text)
    text = _sub(result, ADDRESS_LABEL_RE, ADDRESS, "address", text)
    text = _sub(result, STREET_RE, ADDRESS, "address", text)
    if PHOTO_RE.search(text):
        demo.photo_mentioned = True
    text = _sub(result, PHOTO_RE, PHOTO, "photo", text)

    if demo.age is None and demo.birth_year:
        demo.age = today.year - demo.birth_year if 14 <= today.year - demo.birth_year <= 100 else None

    # Tidy up: one marker for the whole name, no doubled separators.
    text = re.sub(rf"{re.escape(NAME)}(?:[ \t]+{re.escape(NAME)})+", NAME, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;])", r"\1", text)
    text = re.sub(r"([,.;])(?:\s*[,.;])+", r"\1", text)
    result.text = text.strip()
    return result
