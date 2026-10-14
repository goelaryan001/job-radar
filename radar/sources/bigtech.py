"""Custom readers for Big Tech careers sites that don't use a standard platform.

Google, Meta and Apple don't expose a usable feed, so their jobs come in through the
GitHub new-grad lists (Simplify and SpeedyApply track them closely). Amazon, Microsoft
and Netflix are read directly here. These sites change their internals from time to time;
if one breaks, the digest footer will show it as failing and the GitHub lists still cover it.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from ..http import get_json
from ..models import Job, html_to_text

QUERIES = ["new grad", "university graduate", "entry level", "data engineer", "machine learning engineer",
           "data scientist", "data analyst", "business intelligence engineer", "software development engineer",
           "software engineer"]


def _parse_date(s, fmt):
    try:
        return datetime.strptime(s, fmt).timestamp()
    except (TypeError, ValueError):
        return None


def amazon(c):
    out, seen = [], set()
    for q in QUERIES:
        data = get_json(f"https://www.amazon.jobs/en/search.json?base_query={quote(q)}&country=USA"
                        f"&sort=recent&result_limit=50&offset=0")
        for j in data.get("jobs", []):
            jid = str(j.get("id_icims") or j.get("id"))
            if jid in seen:
                continue
            seen.add(jid)
            desc = " ".join(html_to_text(j.get(k, "")) for k in
                            ("description", "basic_qualifications", "preferred_qualifications"))
            out.append(Job(
                source="amazon", source_id=jid, company="Amazon", title=j.get("title", ""),
                url="https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
                location=j.get("normalized_location") or j.get("location", ""),
                posted_at=_parse_date(j.get("posted_date"), "%B %d, %Y"), description=desc, tier="maang",
            ))
    return out


def _eightfold(host, domain, company, tier):
    out, seen = [], set()
    for q in QUERIES:
        data = get_json(f"https://{host}/api/apply/v2/jobs?domain={domain}&start=0&num=20"
                        f"&query={quote(q)}&location=United%20States&sort_by=timestamp")
        for j in data.get("positions", []):
            jid = str(j["id"])
            if jid in seen:
                continue
            seen.add(jid)
            locs = j.get("locations") or [j.get("location", "")]
            out.append(Job(
                source=domain.split(".")[0], source_id=jid, company=company, title=j.get("name", ""),
                url=j.get("canonicalPositionUrl") or f"https://{host}/careers/job/{jid}",
                location=" / ".join(locs), posted_at=j.get("t_create"), tier=tier,
                detail_loader=lambda jid=jid: get_json(
                    f"https://{host}/api/apply/v2/jobs/{jid}?domain={domain}").get("job_description", ""),
            ))
    return out


def microsoft(c):
    # Microsoft's Eightfold site blocks the /api/apply/v2 search but allows /api/pcsx/search.
    # Job details are still served by /api/apply/v2/jobs/<id>.
    host, domain = "apply.careers.microsoft.com", "microsoft.com"
    out, seen = [], set()
    for q in QUERIES:
        for start in (0, 10):   # pcsx returns at most 10 per page
            data = get_json(f"https://{host}/api/pcsx/search?domain={domain}&start={start}&num=10"
                            f"&query={quote(q)}&location=United%20States&sort_by=timestamp")
            for j in (data.get("data") or {}).get("positions", []):
                jid = str(j["id"])
                if jid in seen:
                    continue
                seen.add(jid)
                out.append(Job(
                    source="microsoft", source_id=jid, company="Microsoft", title=j.get("name", ""),
                    url=f"https://{host}/careers/job/{jid}",
                    location=" / ".join(j.get("locations") or []), posted_at=j.get("postedTs"), tier="maang",
                    detail_loader=lambda jid=jid: get_json(
                        f"https://{host}/api/apply/v2/jobs/{jid}?domain={domain}").get("job_description", ""),
                ))
    return out


def netflix(c):
    return _eightfold("explore.jobs.netflix.net", "netflix.com", "Netflix", "maang")


COLLECTORS = {"amazon": amazon, "microsoft": microsoft, "netflix": netflix}
