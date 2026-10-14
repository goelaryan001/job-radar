import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid        TEXT PRIMARY KEY,
    dkey       TEXT,              -- company|title, used to collapse cross-source duplicates
    source     TEXT,
    company    TEXT,
    title      TEXT,
    location   TEXT,
    url        TEXT,
    tier       TEXT,
    posted_at  REAL,
    first_seen REAL,
    last_seen  REAL,
    status     TEXT,              -- match / rejected / dupe
    reason     TEXT,
    role       TEXT,
    resume     TEXT,
    fit        INTEGER,
    skills     TEXT,              -- JSON list
    visa       TEXT,              -- why it was flagged, or NULL
    baseline   INTEGER DEFAULT 0, -- found on the very first run (not "new")
    timing     TEXT               -- "2027" = clearly for 2027 grads; "unclear" = no date stated
);
CREATE INDEX IF NOT EXISTS jobs_dkey ON jobs(dkey);
CREATE INDEX IF NOT EXISTS jobs_status_seen ON jobs(status, first_seen);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS health (
    run_at REAL, source TEXT, ok INTEGER, failed INTEGER, jobs INTEGER, errors TEXT
);
"""


class DB:
    def __init__(self, path="jobs.db"):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        cols = {r[1] for r in self.c.execute("PRAGMA table_info(jobs)")}
        if "timing" not in cols:                       # databases created by an older version
            self.c.execute("ALTER TABLE jobs ADD COLUMN timing TEXT")

    # -- meta -------------------------------------------------------------
    def get(self, k, default=None):
        r = self.c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set(self, k, v):
        self.c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
        self.c.commit()

    # -- jobs -------------------------------------------------------------
    def known_uids(self):
        return {r[0] for r in self.c.execute("SELECT uid FROM jobs")}

    def dkey_owner(self, dkey):
        r = self.c.execute("SELECT uid FROM jobs WHERE dkey=? AND status='match' LIMIT 1", (dkey,)).fetchone()
        return r[0] if r else None

    def touch(self, uids, now):
        self.c.executemany("UPDATE jobs SET last_seen=? WHERE uid=?", [(now, u) for u in uids])

    def insert(self, row: dict):
        row = dict(row)
        if isinstance(row.get("skills"), (list, tuple)):
            row["skills"] = json.dumps(row["skills"])
        cols = ",".join(row)
        self.c.execute(f"INSERT OR IGNORE INTO jobs ({cols}) VALUES ({','.join('?' * len(row))})",
                       list(row.values()))

    def matches_since(self, since, include_baseline_posted=True):
        """Matches that are new since `since`: first seen after it (and not part of the
        first-run baseline), or whose source-reported posting time is after it."""
        q = """SELECT * FROM jobs WHERE status='match' AND (
                   (first_seen >= ? AND baseline = 0) OR (posted_at IS NOT NULL AND posted_at >= ?))"""
        return [dict(r) for r in self.c.execute(q, (since, since))]

    def matches_for_page(self, since):
        q = """SELECT * FROM jobs WHERE status='match' AND
               (first_seen >= ? OR (posted_at IS NOT NULL AND posted_at >= ?))"""
        return [dict(r) for r in self.c.execute(q, (since, since))]

    def prune(self, now, keep_days=45):
        # Forget rejected/dupe rows we have not seen in a long time to keep the file small.
        self.c.execute("DELETE FROM jobs WHERE status!='match' AND last_seen < ?", (now - keep_days * 86400,))
        self.c.execute("DELETE FROM jobs WHERE status='match' AND last_seen < ?", (now - 90 * 86400,))
        self.c.execute("DELETE FROM health WHERE run_at < ?", (now - 14 * 86400,))

    # -- health -----------------------------------------------------------
    def log_health(self, now, source, ok, failed, jobs, errors):
        self.c.execute("INSERT INTO health VALUES (?,?,?,?,?,?)",
                       (now, source, ok, failed, jobs, json.dumps(errors[:15])))

    def latest_health(self, since):
        q = """SELECT source, SUM(ok) ok, SUM(failed) failed, SUM(jobs) jobs, MAX(errors) errors, COUNT(*) runs
               FROM health WHERE run_at >= ? GROUP BY source ORDER BY source"""
        return [dict(r) for r in self.c.execute(q, (since,))]

    def commit(self):
        self.c.commit()

    def close(self):
        self.c.commit()
        self.c.close()


def now():
    return time.time()
