from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Job:
    """One posting, normalized across every source."""
    source: str                 # e.g. "greenhouse", "simplify"
    source_id: str              # id unique within the source
    company: str
    title: str
    url: str
    location: str = ""
    posted_at: Optional[float] = None   # unix seconds, if the source tells us
    description: str = ""               # plain text; may be filled lazily
    tier: Optional[str] = None          # maang / established / other / startup
    newgrad_hint: bool = False          # True when the source itself is a new-grad list
    visa_hint: Optional[str] = None     # source-provided sponsorship info
    # Called to fetch the description only when the title already looks promising.
    detail_loader: Optional[Callable[[], str]] = field(default=None, repr=False, compare=False)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    def load_description(self) -> str:
        import os
        if os.environ.get("RADAR_NO_DETAILS"):      # local testing without network access
            return self.description
        if not self.description and self.detail_loader:
            try:
                self.description = html_to_text(self.detail_loader() or "")
            except Exception:
                self.description = ""
        return self.description


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)          # Greenhouse double-escapes its HTML
    s = re.sub(r"(?i)<\s*(br|/p|/li|/div|/h\d)\s*/?>", "\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|technologies|technology|labs)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()
