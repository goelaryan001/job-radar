import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 25
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 job-radar (personal job alerts)")

_local = threading.local()


def session() -> requests.Session:
    """One pooled session per thread, with polite retries on 429/5xx."""
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "POST"]), respect_retry_after_header=True)
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=8))
        s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
        _local.s = s
    return s


def get_json(url, **kw):
    r = session().get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def post_json(url, payload, **kw):
    r = session().post(url, json=payload, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def get_text(url, **kw):
    r = session().get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.text
