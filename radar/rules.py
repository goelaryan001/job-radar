"""Decides whether a job is a fit, which resume to send, and whether it has visa red flags."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Job, norm

US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
             "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
US_WORDS = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|remote|anywhere|nationwide|" + "|".join([
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "san francisco", "seattle", "los angeles", "bay area", "nyc", "boston", "austin", "chicago", "atlanta",
    "denver", "san jose", "palo alto", "mountain view", "sunnyvale", "redmond", "menlo park", "santa clara"]) + r")\b", re.I)
STATE_CODE = re.compile(r"(?:,|\s-)\s*(" + "|".join(US_STATES) + r")\b")
YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–]|to)?\s*(\d{1,2})?\s*\+?\s*years?(?:'|’)?\s*(?:of\s+)?"
                   r"(?:\w+\s+){0,4}?(?:experience|exp\b)", re.I)


MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_MON = r"(?P<mon>jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t(ember)?)?|oct(ober)?|nov(ember)?|dec(ember)?)"
GRAD_YEAR_TITLE = re.compile(r"\b(202[4-9])\b")
# A year near graduation / new-grad / start wording, e.g. "graduating between Dec 2026 and Jun 2027",
# "2027 new grads", "Class of 2026", "Summer 2027 start".
GRAD_KW = re.compile(r"graduat\w*|\bgrads?\b|class of|degree (?:completion|conferr\w*)|new college grad|"
                     r"start (?:date|in)|cohort|program (?:begins|starts)|\b202[4-9]\s+(?:new\s+|university\s+|college\s+)?grad",
                     re.I)
YEAR = re.compile(r"\b(202[4-9])\b")
START_DATE = re.compile(r"\bstart(?:s|ing)?(?:\s+date)?(?:\s+(?:in|on|of|by))?\s*:?\s+" + _MON + r"\.?,?\s+(?P<y>20\d\d)\b", re.I)
IMMEDIATE = re.compile(r"\b(immediate(ly)? start|start (immediately|asap)|asap\b|available immediately)", re.I)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    role: str = ""
    resume: str = ""
    fit: int = 0
    skills: list = field(default_factory=list)
    visa: str | None = None
    needs_description: bool = False
    timing: str = ""          # "2027" (explicitly for 2027 grads / starts on time) or "unclear"


class Rules:
    def __init__(self, cfg):
        f = cfg["filters"]
        self.roles = [(r["name"], r["resume"], re.compile(r["title"], re.I)) for r in cfg["roles"]]
        self.exclude = re.compile(f["exclude_title"], re.I)
        self.level_two = re.compile(f.get("level_two", r"\b(ii|2)\b"), re.I)
        self.citizenship = {norm(x) for x in f.get("citizenship_companies", [])}
        self.ng_title = re.compile(f["newgrad_title"], re.I)
        self.ng_desc = re.compile(f["newgrad_desc"], re.I)
        self.max_min_years = int(f["max_min_years"])
        self.phd = re.compile(f.get("phd_required", r"$^"), re.I)
        y, mth = cfg.get("graduation", {}).get("earliest_start", "2027-05").split("-")
        self.earliest = (int(y), int(mth))
        self.visa = re.compile(f["visa_flags"], re.I)
        self.non_us = re.compile(f["non_us"], re.I)
        self.skills = {k: [(s, re.compile(r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])", re.I)) for s in v]
                       for k, v in cfg["skills"].items()}
        t = cfg["tiers"]
        self.maang = {norm(x) for x in t["maang"]}
        self.established = {norm(x) for x in t["established"]}

    # -- small helpers ----------------------------------------------------
    def role_of(self, title):
        for name, resume, rx in self.roles:
            if rx.search(title):
                return name, resume
        return None, None

    def is_us(self, location):
        loc = (location or "").strip()
        if not loc:
            return True                      # unknown: keep it, you'll see the location on the page
        parts = re.split(r"\s*/\s*|;\s*|\|\s*", loc)
        for p in parts:
            if US_WORDS.search(p) or STATE_CODE.search(p):
                # "Remote - Canada" style entries are not US
                if re.search(r"remote", p, re.I) and self.non_us.search(p):
                    continue
                return True
        return not any(self.non_us.search(p) for p in parts)

    def min_years(self, text):
        found = []
        for m in YEARS.finditer(text or ""):
            lo = int(m.group(1))
            if lo <= 20:
                found.append(lo)
        return min(found) if found else None

    def tier_of(self, job: Job, company_tier=None):
        n = norm(job.company)
        if n in self.maang or any(n.startswith(x + " ") for x in self.maang):
            return "maang"
        if company_tier:
            return company_tier
        if n in self.established:
            return "established"
        return job.tier or "other"

    # -- stage 1: title only (cheap, before fetching any description) -----
    def title_check(self, job: Job) -> Verdict:
        title = job.title or ""
        role, resume = self.role_of(title)
        if not role:
            return Verdict(False, "role not wanted")
        if self.exclude.search(title):
            return Verdict(False, "seniority/intern/other track in title")
        strong_ng = re.search(r"new grad|early career|university|graduate|campus|20(26|27)", title, re.I)
        if self.level_two.search(title) and not strong_ng:
            return Verdict(False, "level II in title")
        if not self.is_us(job.location):
            return Verdict(False, "outside US")
        ng = job.newgrad_hint or bool(self.ng_title.search(title))
        return Verdict(True, role=role, resume=resume, needs_description=not job.description,
                       reason="newgrad-title" if ng else "")

    # -- stage 2: full check with description -----------------------------
    def timing(self, title, desc):
        """Is this job for your graduation window? Returns ("2027"|"unclear", None) or (None, reason)."""
        grad_year = self.earliest[0]
        years = {int(y) for y in GRAD_YEAR_TITLE.findall(title)}
        # Every year in the same sentence as graduation/start wording counts, so
        # "graduating between December 2026 and June 2027" yields {2026, 2027}.
        for m in GRAD_KW.finditer(desc):
            lo = max(0, m.start() - 25)
            window = re.split(r"(?<=[a-z0-9)])\.\s|\n", desc[lo: m.end() + 90])
            seg = next((w for w in window if m.group(0) in w), desc[lo: m.end() + 90])
            years |= {int(y) for y in YEAR.findall(seg)}
        for m in START_DATE.finditer(title + " " + desc):
            mon = MONTHS[m.group("mon")[:3].lower()]
            if (int(m.group("y")), mon) < self.earliest:
                return None, f"starts {m.group('mon')[:3].title()} {m.group('y')} (before you graduate)"
            years.add(grad_year)
        if grad_year in years:
            return "2027", None
        if years:
            if max(years) < grad_year:
                return None, f"for {max(years)} graduates"
            return None, f"for {min(years)} graduates"
        return "unclear", None

    def full_check(self, job: Job, v: Verdict) -> Verdict:
        desc = job.description or ""
        ng = v.reason == "newgrad-title"
        yrs = self.min_years(desc)
        if yrs is not None and yrs > self.max_min_years:
            return Verdict(False, f"asks for {yrs}+ years")
        if self.phd.search(desc):
            return Verdict(False, "PhD required")
        timing, why = self.timing(job.title, desc)
        if why:
            return Verdict(False, why)
        if not ng:
            if self.ng_desc.search(desc) or (yrs is not None and yrs <= 1):
                ng = True
        if not ng:
            return Verdict(False, "no new-grad signal")
        visa = job.visa_hint
        n = norm(job.company)
        gov_name = re.search(r"\b(defen[cs]e|federal|government|national lab(oratory)?|aerospace|missile|naval|army|air force)\b",
                             job.company or "", re.I)
        if not visa and (gov_name or n in self.citizenship or any(n.startswith(c + " ") for c in self.citizenship)):
            visa = "defense/government contractor: these roles usually require U.S. citizenship or clearance"
        if not visa:
            m = self.visa.search(desc) or self.visa.search(job.title)
            if m:
                visa = "says: “" + desc[max(0, m.start() - 40): m.end() + 40].strip().replace("\n", " ") + "”" \
                    if desc else "title mentions clearance/citizenship"
        hits = [s for s, rx in self.skills.get(v.role, []) if rx.search(desc)]
        total = len(self.skills.get(v.role, [])) or 1
        # Fit is relative: hitting ~40% of your listed skills is already a strong overlap.
        fit = min(100, round(100 * len(hits) / max(6, total * 0.4))) if desc else None
        if timing == "unclear" and IMMEDIATE.search(desc):
            timing = "unclear"          # same bucket; kept explicit for readability
        return Verdict(True, "match", v.role, v.resume, fit if fit is not None else -1, hits[:8], visa,
                       timing=timing)


def dedupe_key(job: Job) -> str:
    t = norm(job.title)
    t = re.sub(r"\b(20\d\d|new grad(uate)?|university grad(uate)?|early career|us|usa|remote)\b", " ", t)
    return f"{norm(job.company)}|{' '.join(t.split())}"
