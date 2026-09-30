import pytest

from app.services import diversity, storage

from .helpers import VACANCY_TEXT


@pytest.mark.parametrize("age,band", [(None, "unknown"), (19, "under_25"), (25, "25_34"), (44, "35_44"),
                                      (54, "45_54"), (70, "55_plus"), (300, "unknown")])
def test_age_band(age, band):
    assert diversity.age_band(age) == band


def seed(app, people):
    """people: list of (gender, age). Returns resume ids in insertion order."""
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        ids = []
        for i, (gender, age) in enumerate(people):
            rid = storage.add_resume(db, sid, f"{i}.txt", f"h{i}", "t", storage.DONE)
            db.execute("INSERT INTO demographics (resume_id, gender, age) VALUES (?, ?, ?)", (rid, gender, age))
            ids.append(rid)
        db.commit()
        return ids


def run(app, pool, shortlist):
    with app.app_context():
        return diversity.report(storage.get_db(), pool, shortlist)


def group(report, dimension, key):
    return next(g for g in report["dimensions"][dimension]["groups"] if g["key"] == key)


def test_small_pool_is_not_reported(app):
    ids = seed(app, [("female", 30)] * 4)
    assert run(app, ids, ids[:2]) == {"insufficient": True, "pool": 4, "shortlist": 2}


def test_four_fifths_rule_flags_underrepresented_group(app):
    ids = seed(app, [("male", 30)] * 6 + [("female", 30)] * 6)
    males, females = ids[:6], ids[6:]
    report = run(app, ids, males[:5] + females[:1])
    female = group(report, "gender", "female")
    male = group(report, "gender", "male")
    assert male["ratio"] == 1.0 and female["ratio"] == 0.2 and female["flagged"]
    assert report["flags"] == [{"dimension": "gender", "group": "female", "ratio": 0.2}]


def test_balanced_shortlist_is_not_flagged(app):
    ids = seed(app, [("male", 30)] * 6 + [("female", 30)] * 6)
    report = run(app, ids, ids[:3] + ids[6:9])
    assert report["flags"] == []


def test_small_groups_are_suppressed_and_excluded_from_ratio(app):
    ids = seed(app, [("male", 30)] * 8 + [("female", 30)] * 2)
    report = run(app, ids, ids[:5])
    female = group(report, "gender", "female")
    assert female["suppressed"] and female["ratio"] is None and report["flags"] == []


def test_unknowns_are_counted_but_never_flagged(app):
    ids = seed(app, [("unknown", None)] * 6 + [("male", 40)] * 5)
    report = run(app, ids, ids[6:])
    unknown = group(report, "gender", "unknown")
    assert unknown["pool"] == 6 and unknown["ratio"] is None
    assert report["dimensions"]["gender"]["known_share"] == pytest.approx(5 / 11)
    assert group(report, "age", "35_44")["pool"] == 5


def test_diversity_panel_renders_on_ranking_page(app, client, csrf, fake_gemini):
    from .helpers import create_session, resume_text, upload

    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [(f"{i}.txt", resume_text(i)) for i in range(6)])
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), session_id)[0]["id"]
    html = client.get(f"/sessions/{session_id}/vacancies/{vid}?top=5").get_data(as_text=True)
    assert "Разнообразие шорт-листа" in html and "Женщины" in html
    assert "female" not in html.split("Разнообразие шорт-листа")[0]  # nothing per candidate in the table
