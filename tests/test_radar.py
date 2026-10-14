"""Offline tests: run with `python -m pytest -q` (no network needed)."""
import time

import pytest
import yaml

from radar import collect as collect_mod
from radar.db import DB
from radar.digest import build_email, build_page
from radar.models import Job
from radar.rules import Rules
from radar.sources import ats, bigtech

CFG = yaml.safe_load(open("config.yaml"))
NOW = time.time()
ISO_NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 3600))

FIXTURES = {
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
        {"id": 1, "title": "Data Engineer, New Grad 2027", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "San Francisco, CA"}, "first_published": ISO_NOW},
        {"id": 2, "title": "Senior Data Engineer", "absolute_url": "u2", "location": {"name": "Remote"}},
        {"id": 3, "title": "Machine Learning Engineer I", "absolute_url": "u3", "location": {"name": "London, UK"}},
        {"id": 4, "title": "Software Engineer", "absolute_url": "u4", "location": {"name": "New York, NY"}},
    ]},
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1": {"content":
        "&lt;p&gt;Join as a new grad. Python, SQL, Airflow, Spark and Snowflake. We sponsor visas.&lt;/p&gt;"},
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4": {"content":
        "<p>5+ years of experience building distributed systems.</p>"},
    "https://api.lever.co/v0/postings/beta?mode=json": [
        {"id": "a", "text": "Data Scientist - University Grad", "hostedUrl": "https://jobs.lever.co/beta/a",
         "createdAt": (NOW - 7200) * 1000, "categories": {"location": "Seattle, WA", "commitment": "Full-time"},
         "descriptionPlain": "Recent graduates welcome. Statistics, A/B testing, SQL, Python.",
         "lists": [{"text": "Requirements", "content": "<li>Must be a U.S. citizen</li>"}]},
        {"id": "b", "text": "Data Science Intern", "hostedUrl": "x", "categories": {"commitment": "Intern"}},
    ],
    "https://api.ashbyhq.com/posting-api/job-board/gamma?includeCompensation=false": {"jobs": [
        {"id": "z", "title": "Software Engineer, Early Career", "jobUrl": "https://jobs.ashbyhq.com/gamma/z",
         "location": "Remote (US)", "employmentType": "FullTime", "publishedAt": ISO_NOW, "isListed": True,
         "descriptionPlain": "0-2 years of experience. Python, Docker, AWS, REST APIs, PostgreSQL."},
    ]},
    "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Ext/jobs": {"jobPostings": [
        {"title": "Deep Learning Engineer - New College Grad 2027", "externalPath": "/job/US-CA/DL_JR1",
         "locationsText": "Santa Clara, CA", "postedOn": "Posted Today"},
        {"title": "Data Analyst", "externalPath": "/job/US-TX/DA_JR2", "locationsText": "Austin, TX",
         "postedOn": "Posted 30+ Days Ago"},
    ]},
    "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Ext/job/US-CA/DL_JR1": {"jobPostingInfo": {
        "jobDescription": "<p>PyTorch, CUDA, deep learning, Python, Docker. Graduating in 2027.</p>"}},
    "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Ext/job/US-TX/DA_JR2": {"jobPostingInfo": {
        "jobDescription": "<p>Entry level. SQL, Tableau, dashboards.</p>"}},
}


def fake_get(url, **kw):
    for k, v in FIXTURES.items():
        if url == k or (url.startswith("https://www.amazon.jobs") and k == "amazon"):
            return v
    raise RuntimeError(f"no fixture for {url}")


def fake_post(url, payload, **kw):
    return fake_get(url)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for mod in (ats, bigtech):
        monkeypatch.setattr(mod, "get_json", fake_get)
    monkeypatch.setattr(ats, "post_json", fake_post)
    monkeypatch.delenv("RADAR_NO_DETAILS", raising=False)


def companies_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"feeds": [], "companies": {
        "greenhouse": [{"name": "Acme", "id": "acme", "tier": "established"}],
        "lever": [{"name": "Beta", "id": "beta", "tier": "startup"}],
        "ashby": [{"name": "Gamma", "id": "gamma", "tier": "startup"}],
        "workday": [{"name": "NVIDIA", "id": "nvidia.wd5/Ext", "tier": "established", "queries": ["new grad"]}],
    }}))
    return str(p)


def run(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    db.set("initialized", NOW - 86400)          # pretend this is not the first run
    collect_mod.collect(CFG, db, companies_file(tmp_path), workers=4, log=lambda *a: None)
    return db


def test_pipeline_end_to_end(tmp_path):
    db = run(tmp_path)
    rows = {r["title"]: r for r in db.matches_since(NOW - 86400)}
    assert "Data Engineer, New Grad 2027" in rows
    assert rows["Data Engineer, New Grad 2027"]["resume"] == "Data Engineer resume"
    assert "Senior Data Engineer" not in rows                       # seniority
    assert "Machine Learning Engineer I" not in rows                # London
    assert "Software Engineer" not in rows                          # 5+ years
    assert "Data Science Intern" not in rows                        # intern commitment
    ds = rows["Data Scientist - University Grad"]
    assert ds["visa"] and "citizen" in ds["visa"].lower()          # visa flag from Lever list content
    assert rows["Software Engineer, Early Career"]["tier"] == "startup"
    assert "Deep Learning Engineer - New College Grad 2027" in rows # workday + detail fetch
    assert rows["Data Engineer, New Grad 2027"]["timing"] == "2027"
    assert rows["Software Engineer, Early Career"]["timing"] == "unclear"
    da = db.c.execute("SELECT baseline FROM jobs WHERE title='Data Analyst'").fetchone()
    if da:                                                          # 30+ days old: stored, but not "new"
        assert da["baseline"] == 1
    assert "Data Analyst" not in rows


def test_second_run_finds_nothing_new(tmp_path):
    db = run(tmp_path)
    before = db.c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    collect_mod.collect(CFG, db, companies_file(tmp_path), workers=4, log=lambda *a: None)
    assert db.c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before


def test_first_run_is_baseline(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    collect_mod.collect(CFG, db, companies_file(tmp_path), workers=4, log=lambda *a: None)
    # Jobs found on the very first run only count as new if the source says they were posted recently.
    titles = {r["title"] for r in db.matches_since(NOW - 86400)}
    assert "Data Engineer, New Grad 2027" in titles                 # has a posting time 1h ago
    assert "Deep Learning Engineer - New College Grad 2027" not in titles   # Workday: no exact time


def test_rules_examples():
    r = Rules(CFG)

    def ok(title, desc="", loc="Seattle, WA", hint=False):
        j = Job("x", "1", "Co", title, "u", loc, description=desc, newgrad_hint=hint)
        v = r.title_check(j)
        return r.full_check(j, v).ok if v.ok else False

    assert ok("Software Engineer, New Grad")
    assert ok("Software Engineering New Grad")
    assert ok("Data Analyst", "This entry-level role ...")
    assert not ok("Data Analyst", "3+ years of experience in SQL")
    assert not ok("Data Analyst", "Entry level. 2+ years of experience in SQL")       # you have 0 full-time years
    assert not ok("Software Engineer - New College Grad 2026")                         # wrong grad year
    assert not ok("Software Engineer, New Grad", "Must graduate by August 2026.")
    assert ok("Software Engineer, New Grad", "Graduating between December 2026 and June 2027.")
    assert not ok("Data Analyst", "Entry level role. Start date: January 2027.")       # before May 2027
    assert not ok("Data Scientist, New Grad", "PhD required.")
    assert not ok("Software Engineer II")
    assert ok("Software Development Engineer II - New Grad / Early Career")
    assert not ok("Embedded Software Engineer", hint=True)
    assert not ok("Software Engineer, New Grad", loc="Toronto, Canada")
    assert ok("Software Engineer, New Grad", loc="Remote - US")
    assert r.role_of("Business Intelligence Engineer")[0] == "Data Analyst"
    assert r.role_of("Machine Learning Engineer")[0] == "ML Engineer"
    assert r.role_of("Analytics Engineer")[0] in ("Data Engineer", "Data Analyst")


def test_digest_and_page(tmp_path, monkeypatch):
    db = run(tmp_path)
    rows = db.matches_since(NOW - 86400)
    subject, body = build_email(rows, db.latest_health(NOW - 86400), "https://x.github.io/job-radar/",
                                NOW, "America/Los_Angeles", 24)
    assert "new job" in subject and "Sponsorship concerns" in body
    assert body.index("Established companies") < body.index("Entry level, start date not stated")
    assert "Entry level, start date not stated" in body                 # Gamma's undated early-career job
    assert "2027 grads" in body
    monkeypatch.chdir(tmp_path)
    (tmp_path / "site").mkdir()
    import shutil, pathlib
    shutil.copy(pathlib.Path(__file__).parent.parent / "site" / "index.html", tmp_path / "site" / "index.html")
    n = build_page(db, CFG, NOW, out_dir=str(tmp_path / "_site"))
    assert n >= 3 and "Data Engineer, New Grad 2027" in (tmp_path / "_site" / "index.html").read_text()
