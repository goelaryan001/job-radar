"""Builds the morning email and the web page from the stored matches."""
from __future__ import annotations

import html
import json
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import DB

TIER_ORDER = {"maang": 0, "established": 1, "other": 2, "startup": 3}
TIER_TITLE = {"maang": "Big Tech (MAANG)", "established": "Established companies",
              "other": "Other companies", "startup": "Startups"}
ROLE_COLOR = {"Data Engineer": "#0b6e4f", "ML Engineer": "#6a3fb5", "Data Scientist": "#b5541c",
              "Data Analyst": "#a8326e", "Software Engineer": "#1f5fbf"}


def ago(ts, now):
    if not ts:
        return ""
    s = max(0, now - ts)
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


def freshness(row):
    """Best guess of when the job went live: the source's posting time, else when we first saw it."""
    return row["posted_at"] or row["first_seen"]


def sort_rows(rows):
    return sorted(rows, key=lambda r: (TIER_ORDER.get(r["tier"], 2), -freshness(r)))


def split(rows, undated_in_main=False):
    """Main list (by tier) / entry-level jobs with no start date / sponsorship concerns."""
    flagged = [r for r in rows if r["visa"]]
    undated = [] if undated_in_main else [r for r in rows if not r["visa"] and r.get("timing") == "unclear"]
    main = [r for r in rows if not r["visa"] and r not in undated]
    groups = {}
    for r in sort_rows(main):
        groups.setdefault(r["tier"] if r["tier"] in TIER_ORDER else "other", []).append(r)
    return groups, sort_rows(undated), sort_rows(flagged)


def undated_in_main(cfg, now):
    d = (cfg.get("graduation") or {}).get("undated_jobs_join_main_list_on", "2027-02-01")
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d") >= d


# ----------------------------------------------------------------------------- email
def _row_html(r, now):
    e = html.escape
    skills = json.loads(r["skills"] or "[]")
    color = ROLE_COLOR.get(r["role"], "#444")
    badge = ('<span style="display:inline-block;margin-left:4px;padding:1px 7px;border-radius:9px;font-size:11px;'
             'color:#0b6e4f;border:1px solid #0b6e4f">2027 grads</span>') if r.get("timing") == "2027" else ""
    when = f"posted {ago(r['posted_at'], now)}" if r["posted_at"] else f"found {ago(r['first_seen'], now)}"
    meta = " · ".join(x for x in [e(r["company"]), e(r["location"] or "")[:60], when] if x)
    extra = ""
    if skills:
        extra += f'<div style="color:#666;font-size:12px;margin-top:2px">matches: {e(", ".join(skills))}</div>'
    if r["visa"]:
        extra += f'<div style="color:#a33;font-size:12px;margin-top:2px">⚠ {e(r["visa"][:160])}</div>'
    return f"""
<tr><td style="padding:10px 0;border-bottom:1px solid #eee">
  <a href="{e(r['url'])}" style="font-size:15px;font-weight:600;color:#111;text-decoration:none">{e(r['title'])}</a>
  <span style="display:inline-block;margin-left:6px;padding:1px 7px;border-radius:9px;font-size:11px;color:#fff;background:{color}">{e(r['resume'] or '')}</span>{badge}
  <div style="color:#444;font-size:13px;margin-top:3px">{meta}</div>{extra}
</td></tr>"""


def build_email(rows, health, page_url, now, tzname, lookback_hours, cfg=None):
    groups, undated, flagged = split(rows, undated_in_main(cfg or {}, now))
    local = datetime.fromtimestamp(now, ZoneInfo(tzname))
    total = len(rows)
    by_role = {}
    for r in rows:
        if not r["visa"]:
            by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    role_line = " · ".join(f"{k}: {v}" for k, v in sorted(by_role.items(), key=lambda x: -x[1]))
    parts = [f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;color:#111">
<h2 style="margin:0 0 4px">{total} new job{'s' if total != 1 else ''} in the last {lookback_hours} hours</h2>
<div style="color:#555;font-size:13px">{local:%A, %B %-d} · {html.escape(role_line) or 'no matches today'}</div>
<div style="margin:12px 0 4px"><a href="{html.escape(page_url)}" style="color:#1f5fbf">Open the full list, with filters and an "applied" checkbox →</a></div>"""]
    for tier in sorted(groups, key=lambda t: TIER_ORDER.get(t, 2)):
        rs = groups[tier]
        parts.append(f'<h3 style="margin:22px 0 0;font-size:16px">{TIER_TITLE.get(tier, tier)} ({len(rs)})</h3><table width="100%" cellspacing="0">')
        parts += [_row_html(r, now) for r in rs]
        parts.append("</table>")
    if undated:
        parts.append(f'<h3 style="margin:26px 0 0;font-size:16px;color:#555">Entry level, start date not stated ({len(undated)})</h3>'
                     '<div style="color:#666;font-size:12px">These don\'t mention 2027 grads or a start date and often want '
                     'someone sooner than May. Worth a look at bigger companies; from February they join the main list.</div>'
                     '<table width="100%" cellspacing="0">')
        parts += [_row_html(r, now) for r in undated]
        parts.append("</table>")
    if flagged:
        parts.append(f'<h3 style="margin:26px 0 0;font-size:16px;color:#a33">Sponsorship concerns ({len(flagged)})</h3>'
                     '<div style="color:#666;font-size:12px">These mention no sponsorship, citizenship or clearance. Check before applying.</div>'
                     '<table width="100%" cellspacing="0">')
        parts += [_row_html(r, now) for r in flagged]
        parts.append("</table>")
    if not rows:
        parts.append('<p style="color:#555">Nothing new matched today. The tracker is still running; quiet days happen.</p>')
    parts.append(_health_html(health))
    parts.append("</div>")
    subject = f"Job Radar: {total} new {'jobs' if total != 1 else 'job'} · {local:%a %b %-d}"
    return subject, "\n".join(parts)


def _health_html(health):
    bad = [h for h in health if h["failed"]]
    lines = []
    for h in health:
        status = "OK" if not h["failed"] else f"{h['failed']} failed fetches"
        lines.append(f"{html.escape(h['source'])}: {status}")
    detail = ""
    if bad:
        errs = []
        for h in bad:
            errs += json.loads(h["errors"] or "[]")[:3]
        detail = "<br>".join(html.escape(e) for e in errs[:8])
    return (f'<div style="margin-top:28px;padding-top:10px;border-top:1px solid #ddd;color:#888;font-size:11px">'
            f'Sources (last 24h): {" · ".join(lines) or "no runs recorded"}'
            f'{"<br><br>Recent errors:<br>" + detail if detail else ""}</div>')


def send_email(subject, body_html, to_addr):
    user, pw = os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"Job Radar <{user}>", to_addr
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [to_addr], msg.as_string())


# ----------------------------------------------------------------------------- web page
def build_page(db: DB, cfg, now, out_dir="_site"):
    rows = db.matches_for_page(now - cfg["digest"]["page_days"] * 86400)
    items = [{
        "id": r["uid"], "title": r["title"], "company": r["company"], "location": r["location"] or "",
        "url": r["url"], "tier": r["tier"] if r["tier"] in TIER_ORDER else "other", "role": r["role"],
        "resume": r["resume"], "skills": json.loads(r["skills"] or "[]"), "visa": r["visa"] or "",
        "posted": r["posted_at"], "found": r["first_seen"], "source": r["source"],
        "timing": r.get("timing") or "unclear",
    } for r in rows]
    tpl = (Path(__file__).parent.parent / "site" / "index.html").read_text()
    payload = json.dumps({"generated": now, "undatedInMain": undated_in_main(cfg, now), "jobs": items}, separators=(",", ":")).replace("</", "<\\/")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "index.html").write_text(tpl.replace("/*__DATA__*/null", payload))
    return len(items)
