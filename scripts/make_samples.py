"""Generate synthetic sample resumes and vacancies in samples/ (RU + KK).

All people and companies are fictional. Some resumes state demographics on purpose so the
anonymizer and the B2 diversity metrics have something to work with.

    pip install -r requirements-dev.txt
    python scripts/make_samples.py [--font C:/Windows/Fonts/arial.ttf]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from docx import Document
from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent / "samples"

# (file stem, format, language, lines)
RESUMES: list[tuple[str, str, str, list[str]]] = [
    ("01_ivanova_python", "pdf", "ru", [
        "Мария Иванова", "Senior Python Developer · Алматы", "Пол: женский. Дата рождения: 14.02.1994",
        "Опыт работы",
        "ТОО «Альфа Софт», Senior Python Developer, 03.2021 – н.в.: микросервисы на FastAPI, PostgreSQL, Redis, Kafka; CI/CD в GitLab.",
        "АО «Бета Финанс», Python Developer, 06.2017 – 02.2021: Django REST API, Celery, Docker.",
        "Образование: КБТУ, бакалавр, Информационные системы.",
        "Навыки: Python, FastAPI, Django, PostgreSQL, Redis, Kafka, Docker, Kubernetes, pytest.",
        "Языки: русский, казахский, английский B2."]),
    ("02_seitkali_python", "docx", "kk", [
        "Ерлан Сейтқали", "Python-әзірлеуші · Астана", "Жынысы: ер. Туған жылы: 1998",
        "Жұмыс тәжірибесі",
        "«Гамма Тех» ЖШС, Python-әзірлеуші, 2022 – қазір: Django, PostgreSQL, Docker, REST API әзірлеу.",
        "«Дельта» стартабы, стажер, 2021 – 2022: Flask, SQLite, парсерлер.",
        "Білімі: Л.Н. Гумилев атындағы ЕҰУ, бакалавр, Бағдарламалық инженерия.",
        "Дағдылар: Python, Django, Flask, PostgreSQL, Docker, Git, Linux.",
        "Тілдер: қазақ, орыс, ағылшын B1."]),
    ("03_petrov_java", "txt", "ru", [
        "Андрей Петров", "Java-разработчик", "Мужчина, 41 год, женат, двое детей.",
        "Опыт: ТОО «Омега Банк», Java Developer, 2012 – н.в.: Spring Boot, Oracle, микросервисы, Kafka.",
        "Образование: КазНУ им. аль-Фараби, магистр, Прикладная математика.",
        "Навыки: Java, Spring, Oracle, Kafka, Docker, немного Python для скриптов.",
        "Языки: русский, английский B1."]),
    ("04_nurlanova_data", "pdf", "kk", [
        "Әйгерім Нұрланова", "Деректер талдаушысы · Алматы",
        "Жұмыс тәжірибесі",
        "«Сигма Ритейл» ЖШС, деректер талдаушысы, 2020 – қазір: SQL, Python (pandas), Power BI дашбордтары, A/B тесттер.",
        "«Каппа Телеком» АҚ, кіші аналитик, 2018 – 2020: Excel, SQL есептері.",
        "Білімі: ҚБТУ, бакалавр, Қолданбалы статистика.",
        "Дағдылар: SQL, Python, pandas, Power BI, Excel, статистика, A/B тестілеу.",
        "Тілдер: қазақ, орыс, ағылшын B2."]),
    ("05_kim_data", "docx", "ru", [
        "Виктория Ким", "Data Analyst", "Пол: женский. Возраст: 26",
        "Опыт: ТОО «Лямбда Маркет», аналитик данных, 2021 – н.в.: SQL (PostgreSQL, ClickHouse), Python, Tableau, продуктовые метрики.",
        "Образование: НУ (Назарбаев Университет), бакалавр, Экономика.",
        "Навыки: SQL, ClickHouse, Python, Tableau, A/B тесты, Airflow (базово).",
        "Языки: русский, английский C1, корейский A2."]),
    ("06_abenov_accountant", "txt", "kk", [
        "Мұрат Әбенов", "Бас бухгалтер", "Отбасылық жағдайы: үйленген. Жасы: 45",
        "Тәжірибе: «Жетісу Құрылыс» ЖШС, бас бухгалтер, 2010 – қазір: 1С:Бухгалтерия, салық есептілігі, ХҚЕС.",
        "«Арай» ЖШС, бухгалтер, 2004 – 2010.",
        "Білімі: Тұран университеті, бакалавр, Есеп және аудит. CAP сертификаты.",
        "Дағдылар: 1С, ХҚЕС, салық кодексі, Excel, қаржылық есептілік.",
        "Тілдер: қазақ, орыс."]),
    ("07_sokolova_accountant", "pdf", "ru", [
        "Елена Соколова", "Бухгалтер", "Дата рождения: 03.07.1990. Замужем.",
        "Опыт: ТОО «Нур Логистик», бухгалтер, 2016 – н.в.: первичная документация, 1С, расчёт зарплаты, сверки с контрагентами.",
        "ИП «Самал», помощник бухгалтера, 2013 – 2016.",
        "Образование: КазЭУ им. Т. Рыскулова, Учёт и аудит.",
        "Навыки: 1С:Бухгалтерия, 1С:ЗУП, налоговые формы 200.00 и 300.00, Excel.",
        "Языки: русский, казахский (разговорный)."]),
    ("08_tulegenov_devops", "docx", "ru", [
        "Нурсултан Тулегенов", "DevOps Engineer",
        "Опыт: ТОО «Эпсилон Клауд», DevOps, 2019 – н.в.: Kubernetes, Terraform, GitLab CI, Prometheus, Grafana; перевёл 40 сервисов в k8s.",
        "Ранее: системный администратор Linux, 2016 – 2019.",
        "Образование: АУЭС, Информационные системы.",
        "Навыки: Kubernetes, Docker, Terraform, Ansible, Linux, Python, Bash, PostgreSQL.",
        "Языки: казахский, русский, английский B2."]),
    ("09_akhmetova_hr", "txt", "ru", [
        "Динара Ахметова", "HR Business Partner", "Пол: женский",
        "Опыт: АО «Тета Групп», HRBP, 2018 – н.в.: подбор ИТ-специалистов, оценка персонала, адаптация, HR-аналитика.",
        "Образование: КИМЭП, магистр, Управление персоналом.",
        "Навыки: рекрутинг, интервью по компетенциям, HR-аналитика, Excel, Power BI.",
        "Языки: русский, казахский, английский C1."]),
    ("10_zhaksylykov_python_junior", "pdf", "kk", [
        "Дәурен Жақсылықов", "Junior Python-әзірлеуші",
        "Тәжірибе: «Йота Лаб» ЖШС, junior әзірлеуші, 2024 – қазір: FastAPI, PostgreSQL, Docker, юнит-тесттер (pytest).",
        "Жобалар: Telegram-бот (aiogram), курстық жоба — Django-дағы онлайн-кітапхана.",
        "Білімі: SDU университеті, бакалавр, Компьютерлік ғылымдар.",
        "Дағдылар: Python, FastAPI, Django, PostgreSQL, Docker, Git, pytest.",
        "Тілдер: қазақ, орыс, ағылшын B2."]),
    ("11_lee_frontend", "docx", "ru", [
        "Александр Ли", "Frontend Developer", "Мне 29 лет.",
        "Опыт: ТОО «Каппа Диджитал», Frontend, 2019 – н.в.: React, TypeScript, Next.js, дизайн-системы.",
        "Образование: МУИТ, Вычислительная техника.",
        "Навыки: JavaScript, TypeScript, React, Next.js, CSS, Figma, немного Node.js.",
        "Языки: русский, английский B2."]),
    ("12_baimukhanova_data", "txt", "kk", [
        "Гүлназ Баймұханова", "Деректер инженері",
        "Тәжірибе: «Мю Энерджи» АҚ, деректер инженері, 2021 – қазір: Airflow, Spark, ClickHouse, dbt, Python ETL.",
        "«Ню Банк», SQL-әзірлеуші, 2019 – 2021: Oracle, PL/SQL.",
        "Білімі: ҚазҰТЗУ, магистр, Ақпараттық жүйелер.",
        "Дағдылар: Python, SQL, Airflow, Spark, ClickHouse, dbt, Docker.",
        "Тілдер: қазақ, орыс, ағылшын B1."]),
    ("13_morozov_sales", "pdf", "ru", [
        "Игорь Морозов", "Менеджер по продажам B2B", "Пол: мужской. Женат.",
        "Опыт: ТОО «Кси Трейд», менеджер B2B продаж, 2015 – н.в.: 10 лет в продажах, выполнение плана 120%, CRM Bitrix24.",
        "Образование: Карагандинский университет, Маркетинг.",
        "Навыки: B2B продажи, переговоры, CRM, холодные звонки, Excel.",
        "Языки: русский, казахский."]),
    ("14_serikbayeva_accountant", "docx", "kk", [
        "Жансая Серікбаева", "Бухгалтер", "Жынысы: әйел",
        "Тәжірибе: «Омикрон Агро» ЖШС, бухгалтер, 2019 – қазір: 1С, ҚҚС есептілігі, жалақы есебі, банк-клиент.",
        "Білімі: Қайнар университеті, Есеп және аудит.",
        "Дағдылар: 1С:Бухгалтерия, 1С:ЗУП, салық есептілігі, Excel, ХҚЕС (негізгі).",
        "Тілдер: қазақ, орыс."]),
    ("15_orlov_python_senior", "txt", "ru", [
        "Дмитрий Орлов", "Lead Python Engineer",
        "Опыт: ТОО «Пи Системс», тимлид Python, 2018 – н.в.: команда из 8 человек, Django, FastAPI, PostgreSQL, Kubernetes, Kafka.",
        "ТОО «Ро Вэб», Python Developer, 2013 – 2018: Django, Celery, MySQL.",
        "Образование: КБТУ, магистр, Компьютерные науки.",
        "Навыки: Python, Django, FastAPI, PostgreSQL, Kafka, Kubernetes, Docker, архитектура, менторство.",
        "Языки: русский, английский C1."]),
    ("16_kassymova_analyst", "pdf", "ru", [
        "Асель Касымова", "Бизнес-аналитик", "Дата рождения: 1987",
        "Опыт: АО «Сигма Страхование», бизнес-аналитик, 2014 – н.в.: сбор требований, BPMN, SQL-выгрузки, Power BI.",
        "Образование: КазГЮУ, Экономика.",
        "Навыки: SQL, Excel, Power BI, BPMN, Jira, Confluence.",
        "Языки: русский, казахский, английский B1."]),
    ("17_oral_devops", "docx", "kk", [
        "Бауыржан Орал", "DevOps инженері", "Жасы: 33",
        "Тәжірибе: «Тау Хостинг» ЖШС, DevOps, 2018 – қазір: Docker, Kubernetes, Ansible, Zabbix, GitHub Actions.",
        "Білімі: Сәтбаев университеті, Телекоммуникация.",
        "Дағдылар: Linux, Docker, Kubernetes, Ansible, Terraform (негізгі), Python, Bash.",
        "Тілдер: қазақ, орыс, ағылшын A2."]),
    ("18_volkova_python", "txt", "ru", [
        "Ольга Волкова", "Python Developer", "Пол: женский. Не замужем.",
        "Опыт: ТОО «Фи Эдтех», Python Developer, 2020 – н.в.: Django, DRF, PostgreSQL, Celery, Docker; покрытие тестами 85%.",
        "Образование: ВКТУ им. Серикбаева, Программная инженерия.",
        "Навыки: Python, Django, DRF, PostgreSQL, Celery, Redis, Docker, pytest.",
        "Языки: русский, английский B2."]),
    ("19_mukanov_data", "pdf", "kk", [
        "Арман Мұқанов", "Кіші деректер талдаушысы", "Жынысы: ер",
        "Тәжірибе: «Хи Логистика» ЖШС, кіші аналитик, 2023 – қазір: SQL есептер, Excel, Looker Studio.",
        "Білімі: Абай атындағы ҚазҰПУ, Математика.",
        "Дағдылар: SQL, Excel, Python (негізгі), Looker Studio.",
        "Тілдер: қазақ, орыс, ағылшын A2."]),
    ("20_ismailova_pm", "docx", "ru", [
        "Камила Исмаилова", "Project Manager (IT)", "Пол: женский. Возраст: 34",
        "Опыт: ТОО «Пси Интегратор», проектный менеджер, 2016 – н.в.: внедрение ERP, Scrum, бюджеты до 500 млн тенге.",
        "Образование: КИМЭП, MBA.",
        "Навыки: Scrum, Kanban, Jira, управление рисками, MS Project, коммуникация со стейкхолдерами.",
        "Языки: русский, казахский, английский C1."]),
]

VACANCIES = {
    "vacancy_python_ru.txt": """Python-разработчик (Middle/Senior)
Мы развиваем платёжную платформу и ищем backend-разработчика.
Обязанности: разработка микросервисов, проектирование API, код-ревью.
Обязательно: Python 3, Django или FastAPI, PostgreSQL, Docker, опыт коммерческой разработки от 3 лет, тесты (pytest).
Будет плюсом: Kafka, Kubernetes, Redis, опыт в финтехе.
Языки: русский; английский не ниже B1 для чтения документации.""",
    "vacancy_data_kk.txt": """Деректер талдаушысы
Бөлшек сауда желісіне деректер талдаушысы қажет.
Міндеттер: сату деректерін талдау, дашбордтар құру, A/B тесттер жүргізу.
Міндетті: SQL (кемінде 2 жыл), Python (pandas), Power BI немесе Tableau, статистика негіздері.
Артықшылық: ClickHouse, Airflow.
Тілдер: қазақ және орыс тілдері; ағылшын B1 — артықшылық.""",
    "vacancy_accountant_biased_ru.txt": """Бухгалтер
В дружный молодой коллектив требуется девушка до 30 лет, приятной внешности, без детей.
Славянской внешности, желательно не замужем.
Обязанности: первичная документация, 1С, расчёт зарплаты, налоговая отчётность.
Требования: опыт от 2 лет, знание 1С:Бухгалтерия и 1С:ЗУП, форм 200.00 и 300.00.""",
}


def write_txt(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_docx(path: Path, lines: list[str]) -> None:
    document = Document()
    document.add_heading(lines[0], level=1)
    for line in lines[1:]:
        document.add_paragraph(line)
    document.core_properties.author = "synthetic"
    document.save(path)


def write_pdf(path: Path, lines: list[str], font: str) -> None:
    pdf = FPDF()
    pdf.set_author("synthetic")
    pdf.add_page()
    pdf.add_font("Body", "", font)
    pdf.set_font("Body", size=18)
    pdf.multi_cell(0, 10, lines[0])
    pdf.set_font("Body", size=11)
    for line in lines[1:]:
        pdf.ln(2)
        pdf.multi_cell(0, 6, line)
    pdf.output(str(path))


def write_scan(path: Path) -> None:
    """A PDF with only vector shapes and no text layer — like a scanned page."""
    pdf = FPDF()
    pdf.add_page()
    for i in range(12):
        pdf.rect(20, 20 + i * 12, 170 - (i % 4) * 25, 6, style="F")
    pdf.output(str(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", default="C:/Windows/Fonts/arial.ttf", help="TTF font with Cyrillic/Kazakh glyphs")
    args = parser.parse_args()

    resumes_dir = ROOT / "resumes"
    bad_dir = ROOT / "bad"
    for folder in (resumes_dir, bad_dir):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)

    for stem, fmt, _lang, lines in RESUMES:
        path = resumes_dir / f"{stem}.{fmt}"
        {"txt": write_txt, "docx": write_docx}.get(fmt, lambda p, l: write_pdf(p, l, args.font))(path, lines)

    for name, text in VACANCIES.items():
        (ROOT / name).write_text(text + "\n", encoding="utf-8")

    write_scan(bad_dir / "scanned_resume.pdf")
    (bad_dir / "renamed_image.pdf").write_bytes(b"\xff\xd8\xff\xe0 not really a pdf")
    (bad_dir / "notes.odt").write_bytes(b"PK\x03\x04 unsupported format")
    print(f"Wrote {len(RESUMES)} resumes, {len(VACANCIES)} vacancies and 3 bad files to {ROOT}")


if __name__ == "__main__":
    main()
