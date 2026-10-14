"""Command line entry point.

    python -m radar run              # hourly job: collect, rebuild page, send digest if it's ~7 AM
    python -m radar collect          # collect only
    python -m radar digest --force   # send the digest right now (for testing)
    python -m radar digest --preview # write digest_preview.html instead of emailing
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from .collect import collect
from .db import DB
from .digest import build_email, build_page, send_email


def load_cfg(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def digest_due(cfg, db, now):
    tz = ZoneInfo(cfg["timezone"])
    local = datetime.fromtimestamp(now, tz)
    after = datetime.strptime(cfg["digest"]["send_after"], "%H:%M").time()
    before = datetime.strptime(cfg["digest"]["send_before"], "%H:%M").time()
    today = local.strftime("%Y-%m-%d")
    return after <= local.time() < before and db.get("last_digest_date") != today, today


def do_digest(cfg, db, now, force=False, preview=False, log=print):
    due, today = digest_due(cfg, db, now)
    if not (due or force or preview):
        log("Digest not due right now.")
        return False
    hours = cfg["digest"]["lookback_hours"]
    # Covers everything since the previous digest (or the last 24h), so nothing slips through
    # if a morning run was late.
    last = float(db.get("last_digest_at", 0) or 0)
    since = min(now - hours * 3600, last) if last else now - hours * 3600
    rows = db.matches_since(since)
    health = db.latest_health(now - 86400)
    page_url = os.environ.get("PAGE_URL", "")
    subject, body = build_email(rows, health, page_url, now, cfg["timezone"], hours, cfg)
    if preview:
        with open("digest_preview.html", "w") as f:
            f.write(body)
        log(f"Wrote digest_preview.html ({len(rows)} jobs). Subject: {subject}")
        return True
    to = os.environ.get("DIGEST_TO") or os.environ.get("GMAIL_ADDRESS")
    send_email(subject, body, to)
    db.set("last_digest_date", today)
    db.set("last_digest_at", now)
    log(f"Sent digest to {to}: {subject}")
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="radar")
    p.add_argument("cmd", choices=["run", "collect", "digest", "page"])
    p.add_argument("--force", action="store_true", help="send the digest even if it is not 7 AM")
    p.add_argument("--preview", action="store_true", help="write digest_preview.html instead of emailing")
    p.add_argument("--db", default=os.environ.get("RADAR_DB", "jobs.db"))
    p.add_argument("--companies", default="companies.yaml")
    a = p.parse_args(argv)

    cfg = load_cfg()
    db = DB(a.db)
    now = time.time()
    failed = False
    if a.cmd in ("run", "collect"):
        collect(cfg, db, a.companies)
    if a.cmd in ("run", "page"):
        n = build_page(db, cfg, now)
        print(f"Built page with {n} jobs.")
    if a.cmd in ("run", "digest"):
        try:
            do_digest(cfg, db, now, force=a.force, preview=a.preview)
        except Exception as e:          # never lose the collected data because email failed
            print(f"Digest failed: {type(e).__name__}: {e}", file=sys.stderr)
            failed = True
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
