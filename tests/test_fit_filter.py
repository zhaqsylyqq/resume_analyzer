"""S5 — the "Подходят" tile opens only suitable candidates; score colours and mobile layout hooks."""
import csv
import io
import re

import pytest

from app.services import storage
from app.services.gemini import GeminiService

from .fakes import PROFILE, FakeClient, scoring_reply
from .helpers import create_session, upload

SCORES = {"alpha": 90, "bravo": 70, "charlie": 50, "delta": 20}


@pytest.fixture()
def mixed(app, client, csrf):
    from app.services.schemas import CandidateProfile

    def handle(call):  # the profile carries the name, scoring reads it back
        if call["config"].response_schema is CandidateProfile:
            name = next(k for k in SCORES if k in call["contents"])
            return {**PROFILE, "full_name": name, "headline": f"Developer band{SCORES[name]}"}
        ids = [int(i) for i in re.search(r"Vacancy ids to assess: ([\d, ]+)", call["contents"]).group(1).split(",")]
        return scoring_reply(ids, int(re.search(r"band(\d+)", call["contents"]).group(1)))
    app.extensions["gemini"] = GeminiService(FakeClient(handler=handle), "m", sleep=lambda s: None)
    sid = create_session(client, csrf)
    upload(client, csrf, sid, [(f"{k}.txt", f"Кандидат {k}. Python, Django, PostgreSQL, опыт 3 года.".encode()) for k in SCORES])
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), sid)[0]["id"]
    return sid, vid


def rows(html):
    return re.findall(r'id="c(\d+)" class="has-status-row"', html)


def test_fit_tile_links_to_suitable_candidates(client, mixed):
    sid, vid = mixed
    html = client.get(f"/sessions/{sid}/vacancies/{vid}?top=10").get_data(as_text=True)
    assert len(rows(html)) == 4
    assert re.search(r'class="stat stat--link stat--fit"[^>]*href="[^"]*top=all[^"]*fit=1[^"]*#ranking-list"', html)
    assert '<div class="stat__value">2</div>' in html  # 90 and 70 are >= 65


def test_fit_filter_shows_only_score_65_and_more(client, mixed):
    sid, vid = mixed
    html = client.get(f"/sessions/{sid}/vacancies/{vid}?top=all&fit=1").get_data(as_text=True)
    assert len(rows(html)) == 2
    assert "Только подходящие (балл от 65)" in html and "Показано 2 из 4" in html
    assert 'stat--fit is-active' in html
    # every other link on the page keeps the filter
    assert 'name="fit" value="1"' in html


def test_fit_tile_toggles_back(client, mixed):
    sid, vid = mixed
    html = client.get(f"/sessions/{sid}/vacancies/{vid}?top=all&fit=1").get_data(as_text=True)
    reset = re.search(r'class="stat stat--link stat--fit is-active"[^>]*href="([^"]+)"', html).group(1)
    assert "fit=" not in reset
    assert len(rows(client.get(reset.replace("&amp;", "&")).get_data(as_text=True))) == 4


def test_fit_filter_combines_with_status_and_exports(client, csrf, app, mixed):
    sid, vid = mixed
    data = client.get(f"/sessions/{sid}/vacancies/{vid}/export.csv?top=all&fit=1").data.decode("utf-8-sig")
    assert len(list(csv.reader(io.StringIO(data), delimiter=";"))) == 3  # header + 2
    with app.app_context():
        best = storage.get_db().execute("SELECT resume_id FROM scores WHERE vacancy_id = ? ORDER BY score DESC", (vid,)).fetchone()[0]
    client.post(f"/sessions/{sid}/vacancies/{vid}/candidates/{best}/status",
                data={"csrf_token": csrf, "status": "shortlist", "top": "all", "fit": "1"})
    html = client.get(f"/sessions/{sid}/vacancies/{vid}?top=all&fit=1&status=shortlist").get_data(as_text=True)
    assert rows(html) == [str(best)]


def test_status_form_returns_to_the_fit_view(client, csrf, app, mixed):
    sid, vid = mixed
    with app.app_context():
        rid = storage.get_db().execute("SELECT resume_id FROM scores WHERE vacancy_id = ?", (vid,)).fetchone()[0]
    r = client.post(f"/sessions/{sid}/vacancies/{vid}/candidates/{rid}/status",
                    data={"csrf_token": csrf, "status": "interview", "top": "all", "fit": "1"})
    assert "fit=1" in r.headers["Location"]


def test_score_is_coloured_by_band(client, mixed):
    sid, vid = mixed
    html = client.get(f"/sessions/{sid}/vacancies/{vid}?top=all").get_data(as_text=True)
    assert html.count("score score--high") == 2 and html.count("score score--mid") == 1 and html.count("score score--low") == 1


def test_css_makes_the_score_bar_visible_and_mobile_safe(app):
    css = (app.static_folder + "/css/app.css")
    text = open(css, encoding="utf-8").read()
    # the fill is a <span>: without display:block its width is ignored and the bar looks empty
    assert re.search(r"\.score__fill \{ display: block;", text)
    assert ".table--cards" in text and "minmax(0, 1fr)" in text
