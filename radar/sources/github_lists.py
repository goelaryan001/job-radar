"""Community-maintained new-grad job lists on GitHub.

These catch companies the tracker doesn't read directly (Google, Meta, Apple, and
thousands of smaller employers). Every job here is already a new-grad posting.
"""
from __future__ import annotations

import re
import time

from ..http import get_json, get_text
from ..models import Job
from .ats import detail_loader_for_url

SIMPLIFY = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"
SPEEDY = "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md"

SIMPLIFY_VISA = {
    "Does Not Offer Sponsorship": "Simplify: does not offer sponsorship",
    "U.S. Citizenship is Required": "Simplify: U.S. citizenship required",
}


def simplify(c=None, max_age_days=21):
    data = get_json(SIMPLIFY)
    cutoff = time.time() - max_age_days * 86400
    out = []
    for j in data:
        if not j.get("active") or not j.get("is_visible", True):
            continue
        if (j.get("date_posted") or 0) < cutoff:
            continue
        cat = (j.get("category") or "").lower()
        if cat in ("hardware", "product"):
            continue
        out.append(Job(
            source="simplify", source_id=str(j["id"]), company=j.get("company_name", ""),
            title=j.get("title", ""), url=j.get("url", ""), location=" / ".join(j.get("locations") or []),
            posted_at=j.get("date_posted"), newgrad_hint=True,
            visa_hint=SIMPLIFY_VISA.get(j.get("sponsorship")),
            detail_loader=detail_loader_for_url(j.get("url", "")),
        ))
    return out


_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|(.*)\|\s*(\d+)d\s*\|\s*$")
_LINK = re.compile(r'href="([^"]+)"')
_TEXT = re.compile(r"<[^>]+>")


def speedyapply(c=None, max_age_days=21):
    md = get_text(SPEEDY)
    out, section = [], ""
    for line in md.splitlines():
        if line.startswith("### "):
            section = line[4:].strip().lower()
            continue
        if not line.startswith("| <a"):
            continue
        m = _ROW.match(line)
        if not m:
            continue
        company_cell, title, location, middle, age = m.groups()
        age = int(age)
        if age > max_age_days:
            continue
        links = _LINK.findall(middle)
        if not links:
            continue
        url = links[-1]
        company = _TEXT.sub("", company_cell).strip()
        out.append(Job(
            source="speedyapply", source_id=url, company=company, title=_TEXT.sub("", title).strip(),
            url=url, location=_TEXT.sub("", location).strip(),
            # SpeedyApply only gives whole days; "0d" means today.
            posted_at=time.time() - age * 86400 if age > 0 else None,
            newgrad_hint=True, tier="established" if "faang" in section else None,
            detail_loader=detail_loader_for_url(url),
        ))
    return out


COLLECTORS = {"simplify": simplify, "speedyapply": speedyapply}
