"""Minimal RU/KK translations. Every key must exist in every language (see tests)."""
from __future__ import annotations

from flask import Flask, current_app, g, request

LANG_COOKIE = "lang"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        "app.name": "Enbek Screen",
        "app.tagline": "ИИ-скрининг резюме",
        "nav.skip": "Перейти к содержимому",
        "nav.main": "Основная навигация",
        "nav.sessions": "Сессии",
        "nav.new_session": "Новый скрининг",
        "nav.theme": "Сменить тему",
        "nav.language": "Язык",
        "home.title": "Отбирайте лучших кандидатов за минуты",
        "home.lead": "Загрузите пачку резюме, опишите вакансию — ИИ извлечёт опыт, оценит соответствие и соберёт рейтинг Top-N. Без учёта пола, возраста и других личных данных.",
        "home.cta": "Начать скрининг",
        "home.feature.batch": "Пакетная загрузка",
        "home.feature.batch.text": "PDF, DOCX и TXT — десятки файлов за раз.",
        "home.feature.fair": "Честная оценка",
        "home.feature.fair.text": "Демографические данные удаляются до оценки.",
        "home.feature.rank": "Рейтинг и экспорт",
        "home.feature.rank.text": "Top-N по каждой вакансии и выгрузка в CSV.",
        "home.recent": "Недавние сессии",
        "home.empty": "Сессий пока нет. Создайте первый скрининг.",
        "error.title": "Что-то пошло не так",
        "error.400": "Некорректный запрос. Обновите страницу и попробуйте снова.",
        "error.403": "Доступ запрещён.",
        "error.404": "Страница не найдена.",
        "error.413": "Файлы слишком большие. Уменьшите размер пакета.",
        "error.500": "Внутренняя ошибка сервера. Мы уже разбираемся.",
        "error.back": "На главную",
        "footer.privacy": "Резюме хранятся локально. В Gemini отправляется только текст для анализа.",
    },
    "kk": {
        "app.name": "Enbek Screen",
        "app.tagline": "Түйіндемелерді ЖИ арқылы іріктеу",
        "nav.skip": "Мазмұнға өту",
        "nav.main": "Негізгі навигация",
        "nav.sessions": "Сессиялар",
        "nav.new_session": "Жаңа іріктеу",
        "nav.theme": "Тақырыпты ауыстыру",
        "nav.language": "Тіл",
        "home.title": "Үздік үміткерлерді бірнеше минутта іріктеңіз",
        "home.lead": "Түйіндемелер топтамасын жүктеп, вакансияны сипаттаңыз — ЖИ тәжірибені анықтап, сәйкестікті бағалайды және Top-N рейтингін құрады. Жынысы, жасы және басқа жеке деректер ескерілмейді.",
        "home.cta": "Іріктеуді бастау",
        "home.feature.batch": "Топтамалық жүктеу",
        "home.feature.batch.text": "PDF, DOCX және TXT — бір уақытта ондаған файл.",
        "home.feature.fair": "Әділ бағалау",
        "home.feature.fair.text": "Демографиялық деректер бағалауға дейін жойылады.",
        "home.feature.rank": "Рейтинг және экспорт",
        "home.feature.rank.text": "Әр вакансия бойынша Top-N және CSV-ге экспорт.",
        "home.recent": "Соңғы сессиялар",
        "home.empty": "Әзірге сессиялар жоқ. Алғашқы іріктеуді жасаңыз.",
        "error.title": "Бірдеңе дұрыс болмады",
        "error.400": "Қате сұраныс. Бетті жаңартып, қайталап көріңіз.",
        "error.403": "Қол жеткізуге тыйым салынған.",
        "error.404": "Бет табылмады.",
        "error.413": "Файлдар тым үлкен. Топтама көлемін азайтыңыз.",
        "error.500": "Сервердің ішкі қатесі. Біз оны шешіп жатырмыз.",
        "error.back": "Басты бетке",
        "footer.privacy": "Түйіндемелер жергілікті сақталады. Gemini-ге тек талдауға арналған мәтін жіберіледі.",
    },
}


def get_locale() -> str:
    if "locale" not in g:
        languages = current_app.config["LANGUAGES"]
        lang = request.cookies.get(LANG_COOKIE)
        g.locale = lang if lang in languages else current_app.config["DEFAULT_LANGUAGE"]
    return g.locale


def translate(key: str, lang: str | None = None, **params: object) -> str:
    lang = lang or get_locale()
    text = TRANSLATIONS.get(lang, {}).get(key) or TRANSLATIONS["ru"].get(key) or key
    return text.format(**params) if params else text


def init_i18n(app: Flask) -> None:
    app.jinja_env.globals["t"] = translate
    app.jinja_env.globals["get_locale"] = get_locale
