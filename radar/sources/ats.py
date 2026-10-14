"""Collectors for the hiring platforms that publish public job feeds.

Each function takes one company entry from companies.yaml and returns a list of Job.
Descriptions are loaded lazily (only for jobs whose title already looks promising),
which keeps each hourly run light.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http import get_json, post_json
from ..models import Job, html_to_text


def _iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- Greenhouse
def greenhouse(c):
    tok = c["id"]
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
    out = []
    for j in data.get("jobs", []):
        jid = j["id"]
        out.append(Job(
            source="greenhouse", source_id=f"{tok}/{jid}", company=c["name"], title=j.get("title", ""),
            url=j.get("absolute_url", ""), location=(j.get("location") or {}).get("name", ""),
            # first_published is when the job went live; updated_at changes on every edit.
            posted_at=_iso(j.get("first_published")),
            detail_loader=lambda tok=tok, jid=jid: get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid}").get("content", ""),
        ))
    return out


# --------------------------------------------------------------------------- Lever
def lever(c):
    tok = c["id"]
    data = get_json(f"https://api.lever.co/v0/postings/{tok}?mode=json")
    out = []
    for j in data:
        cat = j.get("categories") or {}
        commitment = (cat.get("commitment") or "").lower()
        if "intern" in commitment or "part" in commitment or "contract" in commitment:
            continue
        locs = cat.get("allLocations") or [cat.get("location") or ""]
        desc = " ".join([j.get("descriptionPlain") or ""] +
                        [f"{l.get('text','')}: {html_to_text(l.get('content',''))}" for l in j.get("lists", [])] +
                        [j.get("additionalPlain") or ""])
        out.append(Job(
            source="lever", source_id=f"{tok}/{j['id']}", company=c["name"], title=j.get("text", ""),
            url=j.get("hostedUrl", ""), location=" / ".join(l for l in locs if l),
            posted_at=(j.get("createdAt") or 0) / 1000 or None, description=desc,
        ))
    return out


# --------------------------------------------------------------------------- Ashby
def ashby(c):
    tok = c["id"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}?includeCompensation=false")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        et = (j.get("employmentType") or "").lower()
        if et in ("intern", "parttime", "contract", "temporary"):
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote") and not any("remote" in l.lower() for l in locs):
            locs.append("Remote")
        out.append(Job(
            source="ashby", source_id=f"{tok}/{j['id']}", company=c["name"], title=j.get("title", ""),
            url=j.get("jobUrl") or j.get("applyUrl", ""), location=" / ".join(l for l in locs if l),
            posted_at=_iso(j.get("publishedAt")), description=j.get("descriptionPlain") or "",
        ))
    return out


# --------------------------------------------------------------------------- SmartRecruiters
def smartrecruiters(c):
    tok = c["id"]
    out, offset = [], 0
    while offset < 1000:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings"
                        f"?limit=100&offset={offset}&country=us")
        content = data.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            where = ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
            if loc.get("remote"):
                where = (where + " / Remote").strip(" /")
            ref = j.get("ref")
            out.append(Job(
                source="smartrecruiters", source_id=f"{tok}/{j['id']}", company=c["name"],
                title=j.get("name", ""), url=f"https://jobs.smartrecruiters.com/{tok}/{j['id']}",
                location=where, posted_at=_iso(j.get("releasedDate")),
                detail_loader=(lambda ref=ref: " ".join(
                    html_to_text(s.get("text", "")) for s in
                    (get_json(ref).get("jobAd", {}).get("sections", {}) or {}).values()
                    if isinstance(s, dict))) if ref else None,
            ))
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0):
            break
    return out


# --------------------------------------------------------------------------- Workday
WORKDAY_QUERIES = ["new grad", "university graduate", "early career", "entry level", "2027",
                   "data engineer", "machine learning engineer", "data scientist", "data analyst",
                   "software engineer"]


def _workday_age(posted_on: str):
    s = (posted_on or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in s:
        return now.timestamp()
    if "yesterday" in s:
        return (now - timedelta(days=1)).timestamp()
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).timestamp()
    return None


def workday(c):
    """c['id'] looks like 'nvidia.wd5/NVIDIAExternalCareerSite' (host prefix / site name)."""
    host_part, site = c["id"].split("/", 1)
    tenant = host_part.split(".")[0]
    host = f"https://{host_part}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for q in c.get("queries", WORKDAY_QUERIES):
        try:
            data = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q})
        except Exception:
            if not out:
                raise
            continue
        for j in data.get("jobPostings", []):
            path = j.get("externalPath")
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(Job(
                source="workday", source_id=f"{tenant}{path}", company=c["name"], title=j.get("title", ""),
                url=f"{host}/en-US/{site}{path}", location=j.get("locationsText", ""),
                posted_at=None,   # Workday only says "Posted 3 Days Ago"; we rely on first-seen time
                detail_loader=lambda p=path: get_json(f"{api}{p}").get("jobPostingInfo", {}).get("jobDescription", ""),
            ))
            out[-1].workday_age = _workday_age(j.get("postedOn"))
    return out


COLLECTORS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
}


# --------------------------------------------------------------------------- description by URL
# Jobs from the GitHub lists only come with a link. When that link points at a platform we
# can read, fetch the real description so the visa / years-of-experience checks still work.
import threading

_GH = re.compile(r"greenhouse\.io/([\w-]+)/jobs/(\d+)")
_LV = re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
_AB = re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})")
_WD = re.compile(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)(/job/[^?#]+?)(?:/apply)?/?(?:[?#].*)?$")
_ashby_cache, _ashby_lock = {}, threading.Lock()


def _ashby_board(tok):
    with _ashby_lock:
        if tok not in _ashby_cache:
            try:
                data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
                _ashby_cache[tok] = {j["id"]: j.get("descriptionPlain", "") for j in data.get("jobs", [])}
            except Exception:
                _ashby_cache[tok] = {}
        return _ashby_cache[tok]


def detail_loader_for_url(url):
    """Return a function that fetches the job description for this URL, or None."""
    if not url:
        return None
    if m := _GH.search(url):
        tok, jid = m.groups()
        return lambda: get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid}").get("content", "")
    if m := _LV.search(url):
        tok, jid = m.groups()

        def lv():
            j = get_json(f"https://api.lever.co/v0/postings/{tok}/{jid}")
            return " ".join([j.get("descriptionPlain") or ""] +
                            [f"{l.get('text','')}: {html_to_text(l.get('content',''))}" for l in j.get("lists", [])] +
                            [j.get("additionalPlain") or ""])
        return lv
    if m := _AB.search(url):
        tok, jid = m.groups()
        return lambda: _ashby_board(tok).get(jid, "")
    if m := _WD.search(url):
        tenant, wd, site, path = m.groups()
        return lambda: get_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
                                ).get("jobPostingInfo", {}).get("jobDescription", "")
    return None
