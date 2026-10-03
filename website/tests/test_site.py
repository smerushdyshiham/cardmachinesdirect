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
