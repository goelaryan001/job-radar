"""Hourly collection: fetch every source, keep new jobs that fit, store them."""
from __future__ import annotations

import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from .db import DB
from .models import Job
from .rules import Rules, dedupe_key
from .sources import COLLECTORS

STALE_AFTER = 3 * 86400   # a "newly seen" job that was really posted >3 days ago is not new


def load_targets(companies_file="companies.yaml"):
    with open(companies_file) as f:
        data = yaml.safe_load(f)
    targets = [{"name": n, "platform": n, "id": n, "tier": None} for n in data.get("feeds", [])]
    for platform, entries in (data.get("companies") or {}).items():
        for e in entries or []:
            targets.append({"platform": platform, **e})
    return targets


def _fetch(target):
    fn = COLLECTORS[target["platform"]]
    return fn(target)


def collect(cfg, db: DB, companies_file="companies.yaml", workers=16, log=print):
    rules = Rules(cfg)
    targets = load_targets(companies_file)
    now = time.time()
    first_run = db.get("initialized") is None
    known = db.known_uids()

    health = defaultdict(lambda: {"ok": 0, "failed": 0, "jobs": 0, "errors": []})
    fetched: list[tuple[Job, dict]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch, t): t for t in targets}
        for fut in as_completed(futs):
            t = futs[fut]
            h = health[t["platform"]]
            try:
                jobs = fut.result()
                h["ok"] += 1
                h["jobs"] += len(jobs)
                fetched.extend((j, t) for j in jobs)
            except Exception as e:
                h["failed"] += 1
                h["errors"].append(f"{t.get('name', t['id'])}: {type(e).__name__}: {str(e)[:120]}")

    # Jobs we already know: just mark them as still open.
    seen_now = [j.uid for j, _ in fetched if j.uid in known]
    db.touch(seen_now, now)
    fresh = [(j, t) for j, t in fetched if j.uid not in known]
    # The same job can appear twice in one run (e.g. several Workday search queries).
    uniq = {}
    for j, t in fresh:
        uniq.setdefault(j.uid, (j, t))
    fresh = list(uniq.values())

    # Stage 1: title checks (no network).
    stage1 = []
    for j, t in fresh:
        v = rules.title_check(j)
        stage1.append((j, t, v))

    # Stage 2: fetch descriptions only for promising titles, in parallel.
    need = [j for j, t, v in stage1 if v.ok and v.needs_description]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda j: j.load_description(), need))

    counts = defaultdict(int)
    for j, t, v in stage1:
        if v.ok and v.needs_description and j.detail_loader and not j.description and not j.newgrad_hint:
            counts["retry-later"] += 1        # description fetch failed; try again next hour
            continue
        if v.ok:
            v = rules.full_check(j, v)
        tier = rules.tier_of(j, t.get("tier"))
        dkey = dedupe_key(j)
        status = "match" if v.ok else "rejected"
        if v.ok and db.dkey_owner(dkey):
            status, v.reason = "dupe", "already listed from another source"
        posted = j.posted_at or getattr(j, "workday_age", None)
        stale = posted is not None and now - posted > STALE_AFTER
        db.insert({
            "uid": j.uid, "dkey": dkey, "source": j.source, "company": j.company, "title": j.title,
            "location": j.location, "url": j.url, "tier": tier, "posted_at": j.posted_at,
            "first_seen": now, "last_seen": now, "status": status, "reason": v.reason,
            "role": v.role, "resume": v.resume, "fit": v.fit, "skills": v.skills, "visa": v.visa, "timing": v.timing or None,
            "baseline": 1 if (first_run or stale) else 0,
        })
        counts[status] += 1

    for src, h in health.items():
        db.log_health(now, src, h["ok"], h["failed"], h["jobs"], h["errors"])
    if first_run:
        db.set("initialized", now)
    db.set("last_collect", now)
    db.prune(now)
    db.commit()

    log(f"Fetched {len(fetched)} postings from {len(targets)} targets "
        f"({len(fresh)} not seen before): {dict(counts)}")
    for src, h in sorted(health.items()):
        log(f"  {src:16s} ok={h['ok']:<4d} failed={h['failed']:<3d} jobs={h['jobs']}")
        for e in h["errors"][:5]:
            log(f"      ! {e}")
    return counts, health
