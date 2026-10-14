"""Handshake jobs, read from Handshake's own saved-search alert emails in your Gmail.

Handshake has no public feed and its terms forbid scraping, so the tracker never logs in
to Handshake. Instead you save searches in Handshake with email alerts turned on, and this
reads those alert emails (your own inbox) and pulls out the job links.

Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD. If they are missing, this source is skipped.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import time
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from ..models import Job

JOB_LINK = re.compile(r"joinhandshake\.com/.*?(?:jobs|job-search|postings)/(\d+)", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href", ""), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None


def _html_part(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def handshake(c=None, hours=30):
    user, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        return []
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(user, pw)
    m.select("INBOX", readonly=True)
    since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - hours * 3600))
    _, ids = m.search(None, f'(FROM "joinhandshake.com" SINCE {since})')
    out = []
    for num in (ids[0].split() if ids and ids[0] else []):
        _, msg_data = m.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        try:
            sent = parsedate_to_datetime(msg["Date"]).timestamp()
        except Exception:
            sent = time.time()
        p = _Links()
        p.feed(_html_part(msg))
        for href, text in p.links:
            mm = JOB_LINK.search(href)
            if not mm or len(text) < 4 or text.lower() in ("view job", "apply", "view all", "see all jobs"):
                continue
            # Alert emails usually show "Title" with the employer on the next link/line;
            # we keep the title and mark the company as coming from Handshake.
            out.append(Job(
                source="handshake", source_id=mm.group(1), company="(via Handshake)", title=text,
                url=f"https://app.joinhandshake.com/jobs/{mm.group(1)}", location="",
                posted_at=sent, newgrad_hint=True,
            ))
    m.logout()
    return out


COLLECTORS = {"handshake": handshake}
