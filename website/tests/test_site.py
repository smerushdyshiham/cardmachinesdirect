"""Whole-site checks: pages render, confidential rates never leave the server, analytics can't be forged."""
import base64
import html as html_lib
import io
import json
import re
import time
import unittest
import unittest.mock

from _env import PARTNERS_SRC, ROOT, TMP  # noqa: E402  (sets up a throwaway instance folder first)

import app as site  # noqa: E402
from guides import GUIDES  # noqa: E402

PAGES = ["/", "/quote", "/guides/", "/privacy", "/terms", "/how-we-make-money", "/quote/thanks",
         "/sitemap.xml", "/robots.txt"] + [f"/guides/{g['slug']}" for g in GUIDES]
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:test-pass").decode()}


def _token(age=10):
    """A form token as if the quote page had been open for `age` seconds."""
    return site._signer.dumps(time.time() - age)


def _partner_fingerprints():
    """Every way a partner rate could plausibly be written on a page."""
    data = json.loads(PARTNERS_SRC.read_text())
    marks = set()
    for p in data["partners"]:
        for key in ("debit", "credit"):
            pct = p[key] * 100
            marks |= {f"{p[key]}", f"{pct:g}%", f"{pct:.2f}%"}
    return marks


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = site.app.test_client()

    def setUp(self):
        site._hits.clear()  # rate-limit windows must not leak between tests

    def test_every_page_renders(self):
        for path in PAGES:
            with self.subTest(path=path):
                self.assertEqual(self.c.get(path).status_code, 200)

    def test_no_partner_rates_anywhere_public(self):
        marks = _partner_fingerprints()
        blobs = {p: self.c.get(p).get_data(as_text=True) for p in PAGES}
        for f in (ROOT / "static").rglob("*"):
            if f.is_file() and f.suffix in (".js", ".css", ".svg"):
                blobs[str(f)] = f.read_text(encoding="utf-8")
        for volume, atv in ((500, 5), (2000, 20), (10_000, 20), (50_000, 40), (250_000, 150)):
            blobs[f"api {volume}/{atv}"] = self.c.post("/api/estimate", json={"volume": volume, "atv": atv}).get_data(as_text=True)
        for where, text in blobs.items():
            for m in marks:
                with self.subTest(where=where, mark=m):
                    self.assertNotIn(m, text)

    def test_our_row_hides_per_100_and_pence(self):
        d = self.c.post("/api/estimate", json={"volume": 10_000, "atv": 20}).get_json()
        ours = next(r for r in d["rows"] if r["kind"] == "ours")
        self.assertNotIn("per_100", ours)
        self.assertEqual(ours["monthly"], round(ours["monthly"]))
        self.assertTrue(all("per_100" in r for r in d["rows"] if r["kind"] != "ours"))

    def test_instance_folder_not_served(self):
        for path in ("/instance/partners.json", "/static/../instance/partners.json", "/static/%2e%2e/instance/partners.json"):
            with self.subTest(path=path):
                self.assertEqual(self.c.get(path).status_code, 404)

    def test_bad_estimate_input_rejected(self):
        for body in ({"volume": "nan"}, {"volume": "inf"}, {"volume": 0}, {"volume": 1000, "atv": "nan"}):
            with self.subTest(body=body):
                self.assertEqual(self.c.post("/api/estimate", json=body).status_code, 400)

    def test_forged_conversion_is_ignored(self):
        sid = "forge-test"
        self.c.post("/api/events", data=json.dumps({"sid": sid, "page": "/", "attrib": {"source": "emailblaster"},
                                                    "events": [{"type": "form_submit", "page": "/quote"}]}))
        with site.store.conn() as c:
            row = c.execute("SELECT form_submitted FROM sessions WHERE session_id=?", (sid,)).fetchone()
        self.assertEqual(row["form_submitted"], 0)

    def test_quote_submission_stores_lead_estimate_and_attribution(self):
        form = {"name": "T", "business": "Test Ltd", "email": "t@example.com", "knows_fees": "yes",
                "debit_pct": "1", "credit_pct": "2", "auth_p": "1", "monthly_fee": "10", "monthly_volume": "10000",
                "atv": "20", "sid": "lead-test", "ft": _token(), "attrib": json.dumps({"source": "emailblaster", "medium": "email", "campaign": "t"}),
                "statement": (io.BytesIO(b"%PDF-1.4 test"), "s.pdf")}
        r = self.c.post("/quote", data=form, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        lead = site.store.leads(0)[0]
        self.assertEqual(lead["source"], "emailblaster")
        self.assertEqual(json.loads(lead["estimate"])["current_monthly"], 128.0)  # the brief's worked example
        self.assertTrue((site.UPLOADS / lead["statement_file"]).exists())
        with site.store.conn() as c:
            self.assertEqual(c.execute("SELECT form_submitted FROM sessions WHERE session_id='lead-test'").fetchone()[0], 1)

    def test_upload_rejects_fake_pdf(self):
        form = {"name": "T", "business": "B", "email": "t@example.com", "knows_fees": "no", "ft": _token(),
                "statement": (io.BytesIO(b"MZ not a pdf"), "evil.pdf")}
        self.assertEqual(self.c.post("/quote", data=form, content_type="multipart/form-data").status_code, 422)

    def _leads(self):
        return len(site.store.leads(0))

    def _spam_post(self, **extra):
        form = {"name": "Bot", "business": "Spam Ltd", "email": "bot@example.com", "knows_fees": "no", **extra}
        return self.c.post("/quote", data=form)

    def test_spam_without_token_is_dropped(self):
        before = self._leads()
        self.assertEqual(self._spam_post().status_code, 302)
        self.assertEqual(self._spam_post(ft="forged-token").status_code, 302)
        self.assertEqual(self._leads(), before)

    def test_spam_too_fast_or_honeypot_is_dropped(self):
        before = self._leads()
        self._spam_post(ft=_token(age=0))
        self._spam_post(ft=_token(), website="http://spam.example")
        self.assertEqual(self._leads(), before)

    def test_expired_form_asks_to_resend(self):
        old = site._signer.dumps(time.time() - 3 * 86400)
        with unittest.mock.patch.object(site._signer, "loads", side_effect=site.SignatureExpired("old")):
            r = self._spam_post(ft=old)
        self.assertEqual(r.status_code, 422)
        self.assertIn("send it once more", r.get_data(as_text=True))

    def test_quote_posts_are_rate_limited(self):
        codes = [self._spam_post(ft=_token(), email="not-an-email").status_code for _ in range(6)]
        self.assertEqual(codes[:5], [422] * 5)
        self.assertEqual(codes[5], 429)
        site._hits.clear()

    def test_quote_page_has_signed_token(self):
        html = self.c.get("/quote").get_data(as_text=True)
        token = re.search(r'name="ft" value="([^"]+)"', html).group(1)
        self.assertIsInstance(site._form_started(token), float)

    def test_old_statements_are_deleted(self):
        name = "old-statement.pdf"
        (site.UPLOADS / name).write_bytes(b"%PDF old")
        lead_id = site.store.add_lead({"name": "Old", "business": "Old", "email": "o@example.com", "statement_file": name})
        with site.store.conn() as c:
            c.execute("UPDATE leads SET ts=? WHERE id=?", (time.time() - (site.STATEMENT_RETENTION_DAYS + 1) * 86400, lead_id))
        site.purge_old_statements(force=True)
        self.assertFalse((site.UPLOADS / name).exists())
        self.assertIsNone(site.store.lead(lead_id)["statement_file"])

    def test_admin_requires_password_and_journey_handles_empty_values(self):
        self.assertEqual(self.c.get("/admin/").status_code, 401)
        self.c.post("/api/events", data=json.dumps({"sid": "j-test", "page": "/",
                                                    "events": [{"type": "section_time", "section": "hero"},
                                                               {"type": "page_leave", "value": "nan"}]}))
        for path in ("/admin/", "/admin/leads", "/admin/journey/j-test"):
            with self.subTest(path=path):
                self.assertEqual(self.c.get(path, headers=AUTH).status_code, 200)



class SecurityAndSeoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = site.app.test_client()

    def test_http_redirects_to_https(self):
        r = self.c.get("/guides/?x=1", base_url="http://cardmachinesdirect.co.uk")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["Location"], "https://cardmachinesdirect.co.uk/guides/?x=1")
        r = self.c.post("/quote", base_url="http://cardmachinesdirect.co.uk")
        self.assertEqual(r.status_code, 308)  # keeps the POST

    def test_https_behind_proxy_gets_hsts(self):
        r = self.c.get("/", base_url="http://cardmachinesdirect.co.uk", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("max-age=31536000", r.headers["Strict-Transport-Security"])
        for h in ("X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options", "Permissions-Policy"):
            self.assertIn(h, r.headers)

    def test_local_development_is_not_redirected(self):
        self.assertEqual(self.c.get("/", base_url="http://localhost:5000").status_code, 200)

    def test_text_responses_are_gzipped(self):
        for path in ("/", "/static/css/site.css", "/static/js/calculator.js"):
            with self.subTest(path=path), self.c.get(path, headers={"Accept-Encoding": "gzip, br"}) as r:
                self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
                self.assertIn("Accept-Encoding", r.headers.get("Vary", ""))

    def test_no_server_secrets_in_any_response(self):
        secrets_ = {"ADMIN_PASSWORD": "test-pass", "SECRET_KEY": site.app.config["SECRET_KEY"]}
        texts = [self.c.get(p).get_data(as_text=True) for p in PAGES]
        texts += [f.read_text(encoding="utf-8") for f in (ROOT / "static").rglob("*") if f.suffix in (".js", ".css", ".svg", ".webmanifest")]
        texts.append(self.c.post("/api/estimate", json={"volume": 5000}).get_data(as_text=True))
        for name, value in secrets_.items():
            with self.subTest(secret=name):
                self.assertFalse(any(value in t for t in texts))
        for t in texts:  # partner identifiers and the confidential file's name never appear either
            self.assertNotIn("partners.json", t)

    def test_sitemap_and_robots(self):
        xml = self.c.get("/sitemap.xml").get_data(as_text=True)
        for path in ("/terms", "/privacy", "/guides/interchange-fees"):
            self.assertIn(f"https://cardmachinesdirect.co.uk{path}</loc><lastmod>", xml)
        robots = self.c.get("/robots.txt").get_data(as_text=True)
        self.assertIn("Disallow: /admin/", robots)
        self.assertIn("Sitemap: https://cardmachinesdirect.co.uk/sitemap.xml", robots)

    def test_health_check_answers_without_https_redirect(self):
        r = self.c.get("/healthz", base_url="http://10.0.0.5:10000")  # how the host's checker calls it
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "ok"))

    def test_diagnostics_is_admin_only_and_reports_setup(self):
        self.assertEqual(self.c.get("/admin/diagnostics").status_code, 401)
        d = self.c.get("/admin/diagnostics", headers=AUTH).get_json()
        self.assertTrue(d["partners_file_found"])
        self.assertIn("head.html", d["snippets"])
        self.assertNotIn("test-pass", json.dumps(d))  # never echoes secrets

    def test_two_proxy_chain_finds_the_real_visitor_and_resists_spoofing(self):
        """Render: visitor -> Cloudflare -> Render's proxy. A visitor can prepend fake entries, never replace the real one."""
        from werkzeug.middleware.proxy_fix import ProxyFix
        seen = {}
        fixed = ProxyFix(lambda env, start: seen.update(ip=env["REMOTE_ADDR"]) or [], x_for=2)
        for xff, real in (("31.94.14.151, 172.70.243.247", "31.94.14.151"),
                          ("6.6.6.6, 31.94.14.151, 172.70.243.247", "31.94.14.151")):  # "6.6.6.6" typed by the visitor
            fixed({"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": xff}, lambda *a: None)
            self.assertEqual(seen["ip"], real)

    def test_client_ip_header_is_used_for_rate_limits_when_configured(self):
        with unittest.mock.patch.dict("os.environ", {"CLIENT_IP_HEADER": "True-Client-IP"}):
            with site.app.test_request_context(headers={"True-Client-IP": "203.0.113.9"}):
                self.assertEqual(site.client_ip(), "203.0.113.9")
        with site.app.test_request_context(headers={"True-Client-IP": "203.0.113.9"}, environ_base={"REMOTE_ADDR": "198.51.100.1"}):
            self.assertEqual(site.client_ip(), "198.51.100.1")  # header ignored unless configured: visitors could fake it

    def test_404_and_500_pages(self):
        r = self.c.get("/no-such-page")
        self.assertEqual(r.status_code, 404)
        self.assertIn("isn't on the shelf", r.get_data(as_text=True))
        self.assertIn('content="noindex"', r.get_data(as_text=True))
        with site.app.test_request_context():
            body, code = site.server_error(None)
        self.assertEqual(code, 500)
        self.assertIn("Something went wrong on our side", body)

    def test_icons_and_social_card_exist(self):
        html = self.c.get("/").get_data(as_text=True)
        for href in re.findall(r'<link rel="(?:icon|apple-touch-icon|manifest)" href="([^"]+)"', html) + ["/favicon.ico"]:
            with self.subTest(href=href), self.c.get(href) as r:
                self.assertEqual(r.status_code, 200)
        og = re.search(r'property="og:image" content="https://cardmachinesdirect.co.uk([^"]+)"', html).group(1)
        with self.c.get(og) as r:
            self.assertEqual(r.status_code, 200)
            self.assertLess(len(r.data), 100_000)  # social cards should stay small
        self.assertIn('twitter:card" content="summary_large_image"', html)



class LiberalInputTest(unittest.TestCase):
    """Postel's law: accept the ways people actually type amounts."""

    def test_parse_number(self):
        ok = {("10000", True): 10000, ("£10,000", True): 10000, ("10k", True): 10000, ("£10.5K", True): 10500,
              ("1.2m", True): 1200000, (" 20 ", False): 20, ("1.2%", False): 1.2, ("4p", False): 4, ("0.5", False): 0.5}
        for (text, allow_k), want in ok.items():
            with self.subTest(text=text):
                self.assertEqual(site.parse_number(text, allow_k), want)
        for text, allow_k in (("10k", False), ("ten", True), ("nan", True), ("1e9", True), ("-5", True), ("", True)):
            with self.subTest(text=text):
                self.assertIsNone(site.parse_number(text, allow_k))

    def test_quote_form_accepts_k_and_stores_plain_numbers(self):
        site._hits.clear()
        form = {"name": "K", "business": "Ten K Ltd", "email": "k@example.com", "knows_fees": "yes", "ft": _token(),
                "monthly_volume": "£10k", "debit_pct": "1.2%", "auth_p": "4p", "monthly_fee": "£20"}
        self.assertEqual(site.app.test_client().post("/quote", data=form).status_code, 302)
        lead = next(l for l in site.store.leads(0) if l["business"] == "Ten K Ltd")
        self.assertEqual((lead["monthly_volume"], lead["debit_pct"], lead["auth_p"], lead["monthly_fee"]), ("10000", "1.2", "4", "20"))


class LeadEmailTest(unittest.TestCase):
    """Lead alerts: right security mode for the port, sent in the background, never delaying the visitor."""

    ENV = {"SMTP_HOST": "mail.example.com", "SMTP_USER": "alerts@example.com", "SMTP_PASSWORD": "pw",
           "LEADS_EMAIL": "leads@example.com"}

    def _msg(self):
        with unittest.mock.patch.dict("os.environ", self.ENV):
            return site._lead_email(1, self._values(), None, {"source": "s", "medium": "m", "campaign": ""})

    def _values(self, business="Test Ltd"):
        return {"name": "T", "business": business, "email": "t@example.com"}

    def test_port_465_uses_ssl_and_587_uses_starttls(self):
        msg = self._msg()
        for port, ssl_cls, plain_cls in (("465", "SMTP_SSL", None), ("587", None, "SMTP")):
            with self.subTest(port=port), unittest.mock.patch.dict("os.environ", {**self.ENV, "SMTP_PORT": port}),                     unittest.mock.patch("smtplib.SMTP_SSL") as ssl_, unittest.mock.patch("smtplib.SMTP") as plain:
                self.assertTrue(site._send_email(msg, 1))
                used = ssl_ if ssl_cls else plain
                self.assertTrue(used.called)
                self.assertEqual(used.call_args.args[:2], ("mail.example.com", int(port)))
                server = used.return_value.__enter__.return_value
                server.login.assert_called_once_with("alerts@example.com", "pw")
                server.send_message.assert_called_once()
                if plain_cls:
                    plain.return_value.starttls.assert_called_once()
                else:
                    self.assertFalse(plain.called)

    def test_sender_falls_back_to_smtp_user_and_subject_has_no_line_breaks(self):
        with unittest.mock.patch.dict("os.environ", {**self.ENV, "SMTP_FROM": ""}):
            msg = site._lead_email(7, self._values("Evil\r\nBcc: x@y.z"), None, {"source": "s", "medium": "m", "campaign": ""})
        self.assertEqual(msg["From"], "alerts@example.com")
        self.assertEqual(msg["Subject"], "New quote request #7: Evil Bcc: x@y.z")

    def test_failed_email_is_logged_not_raised(self):
        msg = self._msg()
        with unittest.mock.patch.dict("os.environ", {**self.ENV, "SMTP_PORT": "465"}),                 unittest.mock.patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")),                 self.assertLogs("cmd", "ERROR") as logs:
            self.assertFalse(site._send_email(msg, 1))
        self.assertIn("Lead alert email failed for lead 1", logs.output[0])

    def test_visitor_sees_thank_you_without_waiting_for_the_mail_server(self):
        import threading
        release = threading.Event()
        slow_send = lambda msg, lead_id: release.wait(5) or True  # a mail server that takes ages
        form = {"name": "T", "business": "Slow Mail Ltd", "email": "t@example.com", "knows_fees": "no", "ft": _token()}
        with unittest.mock.patch.dict("os.environ", {**self.ENV, "SMTP_PORT": "465"}),                 unittest.mock.patch.object(site, "_send_email", side_effect=slow_send) as sender:
            started = time.time()
            r = site.app.test_client().post("/quote", data=form)
            elapsed = time.time() - started
            release.set()
        self.assertEqual(r.status_code, 302)
        self.assertLess(elapsed, 1.0, "the page waited for the email")
        self.assertTrue(any(l["business"] == "Slow Mail Ltd" for l in site.store.leads(0)), "lead saved first")
        site._mailer.submit(lambda: None).result(5)  # let the background send finish
        sender.assert_called_once()


class InternalTrafficTest(unittest.TestCase):
    """Your own visits and Claude's automated checks are kept apart from real visitors."""

    def setUp(self):
        site._hits.clear()
        self.c = site.app.test_client()

    def _visit(self, client, sid, **payload):
        client.post("/api/events", data=json.dumps({"sid": sid, "page": "/", "attrib": {"source": "direct"},
                                                    "events": [{"type": "page_view"}], **payload}))
        with site.store.conn() as c:
            return c.execute("SELECT internal, internal_reason FROM sessions WHERE session_id=?", (sid,)).fetchone()

    def test_real_visitor_counts_as_real(self):
        row = self._visit(self.c, "real-visitor")
        self.assertEqual((row["internal"], row["internal_reason"]), (0, None))

    def test_opening_admin_marks_this_browser_as_ours(self):
        browser = site.app.test_client()
        r = browser.get("/admin/", headers=AUTH)
        self.assertIn("cmd_internal=1", r.headers.get("Set-Cookie", ""))
        self.assertIn("HttpOnly", r.headers["Set-Cookie"])
        row = self._visit(browser, "rushdy-visit")
        self.assertEqual((row["internal"], row["internal_reason"]), (1, "team browser"))
        self.assertNotIn("cmd_internal", self.c.get("/admin/").headers.get("Set-Cookie", ""), "no cookie without the password")

    def test_automated_browsers_are_internal(self):
        row = self._visit(self.c, "claude-check", auto=1)
        self.assertEqual((row["internal"], row["internal_reason"]), (1, "automated browser"))

    def test_office_ip_is_internal_when_configured(self):
        with unittest.mock.patch.dict("os.environ", {"INTERNAL_IPS": "203.0.113.50, 198.51.100.7"}):
            client = site.app.test_client()
            client.environ_base["REMOTE_ADDR"] = "198.51.100.7"
            row = self._visit(client, "office-visit")
        self.assertEqual(row["internal_reason"], "office IP")

    def test_dashboard_shows_real_visitors_by_default(self):
        self._visit(self.c, "real-a")
        self._visit(self.c, "auto-a", auto=1)
        real = site.store.report(0)["totals"]["sessions"]
        internal = site.store.report(0, audience="internal")["totals"]["sessions"]
        everyone = site.store.report(0, audience="all")["totals"]["sessions"]
        self.assertEqual(real + internal, everyone)
        self.assertGreaterEqual(internal, 1)
        ids = {s["session_id"] for s in site.store.recent_sessions(0, None, 500)}
        self.assertIn("real-a", ids)
        self.assertNotIn("auto-a", ids)
        html = self.c.get("/admin/?show=internal", headers=AUTH).get_data(as_text=True)
        self.assertIn("auto-a", html)
        self.assertIn("Internal · automated browser", html)

    def test_internal_quote_is_flagged_and_email_says_so(self):
        form = {"name": "Me", "business": "My Own Test", "email": "me@example.com", "knows_fees": "no",
                "ft": _token(), "auto": "1"}
        self.c.post("/quote", data=form)
        lead = next(l for l in site.store.leads(0) if l["business"] == "My Own Test")
        self.assertEqual(lead["internal"], 1)
        self.assertNotIn("My Own Test", [l["business"] for l in site.store.leads(0, audience="real")])
        with unittest.mock.patch.dict("os.environ", {"LEADS_EMAIL": "leads@example.com", "SMTP_USER": "a@example.com"}):
            msg = site._lead_email(5, {"business": "My Own Test"}, None, {"source": "s", "medium": "m", "campaign": ""}, internal=True)
        self.assertTrue(msg["Subject"].startswith("[Internal] "))

    def test_mark_and_unmark_from_admin(self):
        self._visit(self.c, "to-mark")
        lead_id = site.store.add_lead({"name": "x", "business": "Linked", "email": "x@example.com", "session_id": "to-mark"})
        same_site = {**AUTH, "Origin": "http://localhost"}
        r = self.c.post("/admin/internal", data={"lead_id": lead_id, "internal": "1", "next": "/admin/leads"}, headers=same_site)
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/admin/leads"))
        self.assertEqual(site.store.lead(lead_id)["internal"], 1)
        with site.store.conn() as c:
            self.assertEqual(c.execute("SELECT internal FROM sessions WHERE session_id='to-mark'").fetchone()[0], 1,
                             "the lead's visit moves with it")
        self.c.post("/admin/internal", data={"session_id": "to-mark", "internal": "0"}, headers=same_site)
        self.assertEqual(site.store.lead(lead_id)["internal"], 0)

    def test_mark_endpoint_rejects_forged_cross_site_posts(self):
        for headers in ({**AUTH, "Origin": "https://evil.example"}, AUTH):
            with self.subTest(headers=list(headers)):
                r = self.c.post("/admin/internal", data={"session_id": "x", "internal": "1"}, headers=headers)
                self.assertEqual(r.status_code, 403)
        r = self.c.post("/admin/internal", data={"session_id": "x", "internal": "1", "next": "https://evil.example/"},
                        headers={**AUTH, "Origin": "http://localhost"})
        self.assertEqual(r.headers["Location"], "/admin/", "never redirects off-site")

    def test_existing_database_is_upgraded_without_losing_data(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from store import Store
        path = Path(tempfile.mkdtemp()) / "old.db"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, visitor_id TEXT, started REAL, last_seen REAL,"
                         " entry_page TEXT, exit_page TEXT, source TEXT, medium TEXT, campaign TEXT, referrer_host TEXT,"
                         " device TEXT, pages INTEGER DEFAULT 0, calc_used INTEGER DEFAULT 0, result_seen INTEGER DEFAULT 0,"
                         " cta_clicked INTEGER DEFAULT 0, quote_viewed INTEGER DEFAULT 0, form_started INTEGER DEFAULT 0,"
                         " form_submitted INTEGER DEFAULT 0);"
                         "INSERT INTO sessions (session_id, started, last_seen, source) VALUES ('old', 1, 1, 'direct');"
                         "CREATE TABLE leads (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, name TEXT, business TEXT);"
                         "INSERT INTO leads (ts, name, business) VALUES (1, 'Old', 'Old Lead');")
        db.commit()
        db.close()
        s = Store(path)
        with s.conn() as c:
            self.assertEqual(c.execute("SELECT internal FROM sessions WHERE session_id='old'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT business, internal FROM leads").fetchone()[:], ("Old Lead", 0))
        Store(path)  # running the upgrade twice is harmless


HTML_PAGES = [p for p in PAGES if not p.endswith((".xml", ".txt"))]


class ContentQualityTest(unittest.TestCase):
    """SEO basics, alt text and links, checked on the rendered HTML of every public page."""

    @classmethod
    def setUpClass(cls):
        c = site.app.test_client()
        cls.c = c
        cls.html = {p: c.get(p).get_data(as_text=True) for p in HTML_PAGES}

    def test_titles_and_descriptions(self):
        titles, descs = {}, {}
        for path, h in self.html.items():
            title = html_lib.unescape(re.search(r"<title>(.*?)</title>", h, re.S).group(1).strip())
            desc = html_lib.unescape(re.search(r'<meta name="description" content="([^"]*)"', h).group(1).strip())
            with self.subTest(path=path):
                self.assertTrue(10 <= len(title) <= 65, f"title length {len(title)}: {title}")
                if "noindex" not in h:
                    self.assertTrue(50 <= len(desc) <= 170, f"description length {len(desc)}: {desc}")
                self.assertIn('<html lang="en-GB">', h)
                self.assertIn('name="viewport"', h)
                self.assertIn('rel="canonical" href="https://cardmachinesdirect.co.uk', h)
                self.assertEqual(len(re.findall(r"<h1[\s>]", h)), 1, "exactly one h1")
                for tag in ("og:title", "og:description", "og:image", "twitter:card"):
                    self.assertIn(tag, h)
            if "noindex" not in h:
                titles.setdefault(title, []).append(path)
                descs.setdefault(desc, []).append(path)
        self.assertEqual({t: p for t, p in titles.items() if len(p) > 1}, {}, "duplicate titles")
        self.assertEqual({d: p for d, p in descs.items() if len(p) > 1}, {}, "duplicate descriptions")

    def test_images_have_alt_and_icons_are_hidden_or_labelled(self):
        for path, h in self.html.items():
            for img in re.findall(r"<img[^>]*>", h):
                with self.subTest(path=path, img=img[:80]):
                    self.assertRegex(img, r'alt="')
            for svg in re.findall(r"<svg[^>]*>", h):
                with self.subTest(path=path, svg=svg[:80]):
                    self.assertTrue('aria-hidden="true"' in svg or ('role="img"' in svg and "aria-label=" in svg))

    def test_company_details_on_every_page(self):
        """Companies Act: name, number, place of registration and registered office on the website."""
        for path, h in self.html.items():
            with self.subTest(path=path):
                for bit in ("ORDUGH FOODS LTD", "15815253", "England and Wales", "128 City Road", "EC1V 2NX"):
                    self.assertIn(bit, h)
        for path in ("/privacy", "/terms"):
            with self.subTest(path=path):
                self.assertNotIn("needs a final check", self.html[path])

    def test_one_label_per_action(self):
        """Law of similarity: the same action is always called the same thing."""
        labels = set()
        for h in self.html.values():
            labels |= {re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r'class="btn[^"]*"[^>]*>(.*?)</(?:a|button)>', h, re.S)}
        quote_and_calc = {l for l in labels if "quote" in l.lower() or "compare" in l.lower()}
        self.assertLessEqual(quote_and_calc, {"Get my exact quote", "Get my quote", "Compare my fees", "Send for my quote"}, quote_and_calc)

    def test_internal_links_and_anchors_resolve(self):
        checked = {}
        for path, h in self.html.items():
            for href in set(re.findall(r'href="([^"]+)"', h)):
                if href.startswith(("http", "mailto:", "tel:")) or href.startswith("/static/") and "?" in href:
                    continue
                target, _, frag = href.partition("#")
                target = target or path
                if target not in checked:
                    with self.c.get(target) as r:
                        checked[target] = (r.status_code, r.get_data(as_text=True) if r.mimetype == "text/html" else "")
                status, body = checked[target]
                with self.subTest(page=path, href=href):
                    self.assertLess(status, 400)
                    if frag:
                        self.assertRegex(body, rf'id="{re.escape(frag)}"', "anchor target missing")


if __name__ == "__main__":
    unittest.main()
