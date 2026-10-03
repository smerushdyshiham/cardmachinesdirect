"""SQLite storage for journey analytics and quote requests."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id     TEXT PRIMARY KEY,
  visitor_id     TEXT,
  started        REAL,
  last_seen      REAL,
  entry_page     TEXT,
  exit_page      TEXT,
  source         TEXT,
  medium         TEXT,
  campaign       TEXT,
  referrer_host  TEXT,
  device         TEXT,
  pages          INTEGER DEFAULT 0,
  calc_used      INTEGER DEFAULT 0,
  result_seen    INTEGER DEFAULT 0,
  cta_clicked    INTEGER DEFAULT 0,
  quote_viewed   INTEGER DEFAULT 0,
  form_started   INTEGER DEFAULT 0,
  form_submitted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          REAL,
  session_id  TEXT,
  type        TEXT,
  page        TEXT,
  section     TEXT,
  value       REAL,
  data        TEXT
);
CREATE INDEX IF NOT EXISTS ev_session ON events(session_id);
CREATE INDEX IF NOT EXISTS ev_type_ts ON events(type, ts);
CREATE TABLE IF NOT EXISTS leads (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              REAL,
  name            TEXT,
  business        TEXT,
  email           TEXT,
  phone           TEXT,
  business_type   TEXT,
  provider        TEXT,
  knows_fees      TEXT,
  debit_pct       TEXT,
  credit_pct      TEXT,
  auth_p          TEXT,
  monthly_fee     TEXT,
  monthly_volume  TEXT,
  message         TEXT,
  statement_file  TEXT,
  statement_name  TEXT,
  session_id      TEXT,
  visitor_id      TEXT,
  source          TEXT,
  medium          TEXT,
  campaign        TEXT,
  estimate        TEXT
);
"""

# Funnel flags a session can earn, keyed by the event that sets them.
FLAG_FOR_EVENT = {
    "calc_change": "calc_used",
    "calc_result": "result_seen",
    "cta_click": "cta_clicked",
    "quote_view": "quote_viewed",
    "form_start": "form_started",
    "form_submit": "form_submitted",
}

ALLOWED_EVENTS = {
    "page_view", "page_leave", "section_view", "section_time", "calc_change", "calc_result",
    "current_fees_open", "atv_open", "cta_click", "link_click", "quote_view", "form_start",
    "form_field", "form_submit", "form_error", "scroll_depth",
}
SERVER_ONLY_EVENTS = {"form_submit"}
TIMED_EVENTS = {"section_time", "page_leave", "scroll_depth"}


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- analytics ingest -------------------------------------------------
    def record(self, payload: dict, user_agent: str = "", trusted: bool = False) -> None:
        """trusted=True only for server-side calls: conversions must not be forgeable via /api/events."""
        sid = _clip(payload.get("sid"), 64)
        if not sid:
            return
        now = time.time()
        page = _clip(payload.get("page"), 300)
        attrib = payload.get("attrib")
        attrib = attrib if isinstance(attrib, dict) else {}  # public endpoint: never trust the shape
        events = payload.get("events")
        events = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        with self.conn() as c:
            row = c.execute("SELECT session_id FROM sessions WHERE session_id=?", (sid,)).fetchone()
            if row is None:
                # OR IGNORE: a visitor's first two batches can arrive at once; the loser just joins the session.
                c.execute(
                    "INSERT OR IGNORE INTO sessions (session_id, visitor_id, started, last_seen, entry_page, exit_page,"
                    " source, medium, campaign, referrer_host, device) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid, _clip(payload.get("vid"), 64), now, now, page, page,
                        _clip(attrib.get("source"), 80) or "direct",
                        _clip(attrib.get("medium"), 80) or "none",
                        _clip(attrib.get("campaign"), 120),
                        _host(attrib.get("referrer")),
                        "mobile" if "Mobi" in user_agent else "desktop",
                    ),
                )
            for ev in events[:50]:
                etype = ev.get("type")
                if etype not in ALLOWED_EVENTS or (etype in SERVER_ONLY_EVENTS and not trusted):
                    continue
                if etype in TIMED_EVENTS and _num(ev.get("value")) is None:
                    continue  # a timing event without a duration would break the reports
                epage = _clip(ev.get("page"), 300) or page
                c.execute(
                    "INSERT INTO events (ts, session_id, type, page, section, value, data) VALUES (?,?,?,?,?,?,?)",
                    (now, sid, etype, epage, _clip(ev.get("section"), 60), _num(ev.get("value")),
                     json.dumps(ev.get("data"))[:2000] if ev.get("data") is not None else None),
                )
                flag = FLAG_FOR_EVENT.get(etype)
                if flag:
                    c.execute(f"UPDATE sessions SET {flag}=1 WHERE session_id=?", (sid,))
                if etype == "page_view":
                    c.execute("UPDATE sessions SET pages=pages+1, exit_page=? WHERE session_id=?", (epage, sid))
            c.execute("UPDATE sessions SET last_seen=? WHERE session_id=?", (now, sid))

    # ---- leads ------------------------------------------------------------
    def add_lead(self, lead: dict) -> int:
        cols = [
            "name", "business", "email", "phone", "business_type", "provider", "knows_fees", "debit_pct",
            "credit_pct", "auth_p", "monthly_fee", "monthly_volume", "message", "statement_file",
            "statement_name", "session_id", "visitor_id", "source", "medium", "campaign", "estimate",
        ]
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO leads (ts, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})",
                [time.time()] + [lead.get(k) for k in cols],
            )
            return cur.lastrowid

    def leads(self, since: float, source: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM leads WHERE ts >= ?", [since]
        if source:
            q += " AND source = ?"
            args.append(source)
        with self.conn() as c:
            return c.execute(q + " ORDER BY ts DESC", args).fetchall()

    def expire_statements(self, before: float) -> list[str]:
        """Detach statements from leads older than `before`; returns the file names to delete."""
        with self.conn() as c:
            rows = c.execute("SELECT id, statement_file FROM leads WHERE ts < ? AND statement_file IS NOT NULL",
                             (before,)).fetchall()
            c.executemany("UPDATE leads SET statement_file=NULL WHERE id=?", [(r["id"],) for r in rows])
        return [r["statement_file"] for r in rows]

    def expire_analytics(self, before: float) -> None:
        """Drop visit statistics older than `before` (the retention period in the privacy notice)."""
        with self.conn() as c:
            c.execute("DELETE FROM events WHERE ts < ?", (before,))
            c.execute("DELETE FROM sessions WHERE last_seen < ?", (before,))

    def lead(self, lead_id: int):
        with self.conn() as c:
            return c.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

    # ---- reporting --------------------------------------------------------
    def report(self, since: float, source: str | None = None) -> dict:
        where, args = "s.started >= ?", [since]
        if source:
            where += " AND s.source = ?"
            args.append(source)
        with self.conn() as c:
            q = lambda sql, extra=(): c.execute(sql.format(w=where), [*args, *extra]).fetchall()  # noqa: E731
            totals = q(
                "SELECT COUNT(*) sessions, COUNT(DISTINCT visitor_id) visitors,"
                " SUM(calc_used) calc_used, SUM(result_seen) result_seen, SUM(cta_clicked) cta_clicked,"
                " SUM(quote_viewed) quote_viewed, SUM(form_started) form_started, SUM(form_submitted) form_submitted,"
                " AVG(last_seen - started) avg_duration, AVG(pages) avg_pages"
                " FROM sessions s WHERE {w}"
            )[0]
            by_source = q(
                "SELECT source, medium, COUNT(*) sessions, SUM(calc_used) calc_used,"
                " SUM(quote_viewed) quote_viewed, SUM(form_submitted) form_submitted,"
                " AVG(last_seen - started) avg_duration"
                " FROM sessions s WHERE {w} GROUP BY source, medium ORDER BY sessions DESC LIMIT 25"
            )
            campaigns = q(
                "SELECT source, campaign, COUNT(*) sessions, SUM(form_submitted) form_submitted"
                " FROM sessions s WHERE {w} AND campaign IS NOT NULL AND campaign != ''"
                " GROUP BY source, campaign ORDER BY sessions DESC LIMIT 25"
            )
            entries = q(
                "SELECT entry_page page, COUNT(*) n, SUM(form_submitted) converted FROM sessions s"
                " WHERE {w} GROUP BY entry_page ORDER BY n DESC LIMIT 15"
            )
            exits = q(
                "SELECT exit_page page, COUNT(*) n FROM sessions s WHERE {w} AND form_submitted = 0"
                " GROUP BY exit_page ORDER BY n DESC LIMIT 15"
            )
            sections = q(
                "SELECT e.page, e.section, COUNT(DISTINCT e.session_id) sessions, SUM(e.value)/1000.0 total_s"
                " FROM events e JOIN sessions s ON s.session_id = e.session_id"
                " WHERE {w} AND e.type = 'section_time' GROUP BY e.page, e.section ORDER BY e.page, sessions DESC"
            )
            pages = q(
                "SELECT e.page, COUNT(*) views, AVG(e.value)/1000.0 avg_s FROM events e"
                " JOIN sessions s ON s.session_id = e.session_id"
                " WHERE {w} AND e.type = 'page_leave' GROUP BY e.page ORDER BY views DESC LIMIT 25"
            )
            daily = q(
                "SELECT date(s.started, 'unixepoch') day, COUNT(*) sessions, SUM(form_submitted) leads"
                " FROM sessions s WHERE {w} GROUP BY day ORDER BY day"
            )
            devices = q("SELECT device, COUNT(*) n FROM sessions s WHERE {w} GROUP BY device")
            sources = [r[0] for r in c.execute("SELECT DISTINCT source FROM sessions ORDER BY source").fetchall()]
        return {
            "totals": dict(totals), "by_source": by_source, "campaigns": campaigns, "entries": entries,
            "exits": exits, "sections": sections, "pages": pages, "daily": daily, "devices": devices,
            "sources": sources,
        }

    def journey(self, session_id: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                "SELECT ts, type, page, section, value, data FROM events WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()

    def recent_sessions(self, since: float, source: str | None, limit: int = 50) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM sessions WHERE started >= ?", [since]
        if source:
            q += " AND source = ?"
            args.append(source)
        with self.conn() as c:
            return c.execute(q + " ORDER BY started DESC LIMIT ?", [*args, limit]).fetchall()


def _clip(value, n):
    if value is None:
        return None
    return str(value)[:n]


def _num(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None  # SQLite stores NaN as NULL, which breaks the reports


def _host(url):
    if not url:
        return None
    try:
        return urlparse(str(url)).hostname
    except ValueError:
        return None
