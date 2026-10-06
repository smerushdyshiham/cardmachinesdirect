"""End-to-end tests in a real browser (Chromium via Playwright): visitor journeys, consent, accessibility,
mobile layout and speed.

Needs:  pip install playwright  &&  python -m playwright install chromium
The suite is skipped (not failed) if the browser isn't installed.
"""
import json
import logging
import re
import threading
import unittest

from _env import ROOT  # noqa: F401  (sets up a throwaway instance folder first)

import app as site
from guides import GUIDES

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

AXE = (ROOT / "tests" / "vendor" / "axe.min.js").read_text(encoding="utf-8")
PUBLIC = ["/", "/quote", "/guides/", "/privacy", "/terms", "/how-we-make-money", "/quote/thanks"] + \
         [f"/guides/{g['slug']}" for g in GUIDES]
WIDTHS = {"desktop": (1366, 900), "tablet": (768, 1024), "phone": (390, 844), "small phone": (360, 740),
          "iphone se": (375, 667), "tiny phone": (320, 568)}
PDF = b"%PDF-1.4\n% test statement\n"


def setUpModule():
    global PW, BROWSER, SERVER, BASE
    if sync_playwright is None:
        raise unittest.SkipTest("playwright not installed")
    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    site.app.config["RATE_LIMITS"] = False   # dozens of page loads from one IP would trip the limits
    site.MIN_FORM_SECONDS = 0                # tests fill the form faster than a person can
    SERVER = make_server("127.0.0.1", 0, site.app, threaded=True)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_port}"
    PW = sync_playwright().start()
    try:
        BROWSER = PW.chromium.launch()
    except Exception as e:  # browser binaries not downloaded
        PW.stop()
        raise unittest.SkipTest(f"chromium not available: {e}")


def tearDownModule():
    BROWSER.close()
    PW.stop()
    SERVER.shutdown()
    site.app.config["RATE_LIMITS"] = True
    site.MIN_FORM_SECONDS = 3


class BrowserCase(unittest.TestCase):
    def new_page(self, width="desktop", **ctx):
        w, h = WIDTHS[width]
        context = BROWSER.new_context(viewport={"width": w, "height": h}, **ctx)
        self.addCleanup(context.close)
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(f"js error: {e}"))
        page.on("console", lambda m: page.errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        return page

    def go(self, page, path, **kw):
        resp = page.goto(BASE + path, **kw)
        page.wait_for_load_state("networkidle")
        return resp

    def axe(self, page):
        page.add_script_tag(content=AXE)
        found = page.evaluate("""async () => (await axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21aa', 'best-practice']}))
            .violations.map(v => ({id: v.id, nodes: v.nodes.map(n => ({target: n.target.join(' '), html: n.html}))}))""")
        problems = []
        for v in found:
            # The "Direct" in the wordmark is a logo: WCAG 1.4.3 exempts logotypes from contrast rules.
            nodes = [n for n in v["nodes"] if not (v["id"] == "color-contrast" and n["html"].startswith("<span>Direct</span>"))]
            problems += [f'{v["id"]}: {n["target"]}' for n in nodes]
        return problems

    def calc_result(self, page):
        page.wait_for_selector("[data-results]:not(.is-loading)")
        return page.locator('#compare [data-out="rows"]').inner_text()

    def _banner(self, page):
        if not page.is_visible("#consent"):  # phones: the banner waits for the first scroll
            page.mouse.wheel(0, 200)
            page.wait_for_selector("#consent", state="visible")

    def decline_cookies(self, page):
        self._banner(page)
        page.click('[data-consent="no"]')

    def accept_cookies(self, page):
        self._banner(page)
        page.click('[data-consent="yes"]')


class LayoutAndQualityTest(BrowserCase):
    def test_every_page_at_every_width_has_no_errors_or_sideways_scroll(self):
        for name in WIDTHS:
            page = self.new_page(name)
            for path in PUBLIC + ["/no-such-page"]:
                with self.subTest(width=name, path=path):
                    page.errors.clear()
                    resp = self.go(page, path)
                    self.assertEqual(resp.status, 404 if path == "/no-such-page" else 200)
                    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                    self.assertLessEqual(overflow, 0, "page scrolls sideways")
                    errors = page.errors if path != "/no-such-page" else [e for e in page.errors if "404" not in e]
                    self.assertEqual(errors, [])

    def test_accessibility_on_every_page_desktop_and_phone(self):
        for name in ("desktop", "phone"):
            page = self.new_page(name)
            for path in PUBLIC + ["/no-such-page"]:
                with self.subTest(width=name, path=path):
                    self.go(page, path)
                    self.assertEqual(self.axe(page), [])

    def test_accessibility_of_interactive_states(self):
        page = self.new_page("phone")
        self.go(page, "/")
        self.decline_cookies(page)
        page.evaluate("document.querySelectorAll('#compare details').forEach(d => d.open = true)")
        with self.subTest(state="calculator panels open"):
            self.assertEqual(self.axe(page), [])
        for sel, v in (("#cur-debit", "0.2"), ("#cur-credit", "0.2"), ("#cur-auth", "0"), ("#cur-monthly", "0")):
            page.fill(sel, v)
        self.calc_result(page)
        with self.subTest(state="we can't beat your deal"):
            self.assertEqual(self.axe(page), [])
        self.go(page, "/quote")
        page.click("button[type=submit]")
        with self.subTest(state="form errors showing"):
            self.assertEqual(self.axe(page), [])

    def test_admin_pages_are_accessible(self):
        page = self.new_page(http_credentials={"username": "admin", "password": "test-pass"})
        for path in ("/admin/", "/admin/leads"):
            with self.subTest(path=path):
                self.go(page, path)
                self.assertEqual(self.axe(page), [])

    def test_keyboard_only_visitor(self):
        page = self.new_page()
        self.go(page, "/")
        self.decline_cookies(page)
        page.keyboard.press("Tab")
        skip = page.evaluate("document.activeElement.textContent.trim()")
        self.assertEqual(skip, "Skip to content", "first Tab lands on the skip link")
        self.assertTrue(page.evaluate("document.activeElement.getBoundingClientRect().top >= 0"), "skip link visible on focus")
        # Every focusable control shows a visible focus ring.
        missing = page.evaluate("""() => [...document.querySelectorAll('a[href], button, input, select, summary, [tabindex="0"]')]
            .filter(el => el.offsetParent).slice(0, 60).filter(el => { el.focus({focusVisible: true});
              // Sliders draw their focus ring on the thumb (a pseudo-element computed styles can't see).
              const s = getComputedStyle(el); return el.type !== 'range' && el === document.activeElement && s.outlineStyle === 'none' && s.boxShadow === 'none'; })
            .map(el => el.outerHTML.slice(0, 80))""")
        self.assertEqual(missing, [], "controls with no visible focus style")
        # The calculator slider works with arrow keys alone.
        slider = page.locator('#compare [data-input="volume-slider"]')
        slider.focus()
        before = page.locator('#compare [data-input="volume-text"]').input_value()
        for _ in range(25):
            page.keyboard.press("ArrowRight")
        self.calc_result(page)
        self.assertNotEqual(page.locator('#compare [data-input="volume-text"]').input_value(), before)

    def test_saving_and_quote_button_on_the_first_screen(self):
        """Peak-end / Fitts: the saving and the next step are visible without scrolling, on every phone size."""
        for name in ("desktop", "phone", "small phone", "iphone se"):
            page = self.new_page(name)
            self.go(page, "/")
            box = page.evaluate("""() => { const t = document.querySelector('.hero-strip .save-tag').getBoundingClientRect(),
                c = document.querySelector('.strip-cta .btn').getBoundingClientRect(), b = document.getElementById('consent');
                const bannerTop = b && !b.hidden ? b.getBoundingClientRect().top : innerHeight;
                const clash = [...document.querySelectorAll('.strip-price *')].some(e => { const r = e.getBoundingClientRect();
                    return r.width && r.right > t.left + 2 && r.left < t.right && r.bottom > t.top && r.top < t.bottom; });
                return {tag: t.bottom, cta: c.bottom, ctaRight: c.right, bannerTop, bannerRight: b && !b.hidden ? b.getBoundingClientRect().right : 0, vh: innerHeight, clash}; }""")
            with self.subTest(width=name):
                self.assertLessEqual(box["tag"], min(box["vh"], box["bannerTop"]), "saving below the fold or under the banner")
                self.assertLessEqual(box["cta"], box["vh"], "quote button below the fold")
                if box["cta"] > box["bannerTop"]:
                    self.assertGreater(box["ctaRight"] - 250, box["bannerRight"], "quote button hidden under the banner")
                self.assertFalse(box["clash"], "price text runs under the saving tag")

    def test_hero_quote_button_carries_takings(self):
        page = self.new_page("phone")
        self.go(page, "/")
        page.evaluate("""() => { const s = document.querySelector('#hero-volume'); s.value = 700; s.dispatchEvent(new Event('input', {bubbles: true})); }""")
        self.calc_result(page)
        page.click(".strip-cta .btn")
        page.wait_for_url("**/quote**")
        self.assertRegex(page.url, r"volume=\d+")
        self.assertNotEqual(page.input_value("#f-monthly_volume"), "")

    def test_controls_are_at_least_44px(self):
        """Fitts: every control (not links inside sentences) is a comfortable tap target on a phone."""
        for path in ("/", "/quote", "/guides/card-machine-fees"):
            page = self.new_page("phone")
            self.go(page, path)
            self.decline_cookies(page)
            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            small = page.evaluate("""() => [...document.querySelectorAll('.btn, details.more > summary, .faq summary, .choice, .nav a, .strip-foot a, .shelf li[tabindex], input[type=range]')]
                .filter(e => e.offsetParent).map(e => [e.innerText.trim().slice(0, 30) || e.className, Math.round(e.getBoundingClientRect().height)])
                .filter(([, h]) => h < 44 && h > 0).filter(([t]) => !String(t).includes('range'))""")
            with self.subTest(path=path):
                self.assertEqual(small, [])

    def test_competitor_notes_open_on_tap(self):
        page = self.new_page("phone")
        self.go(page, "/")
        self.decline_cookies(page)
        self.calc_result(page)
        row = page.locator('#compare [data-out="rows"] li[tabindex]').first
        row.focus()
        page.wait_for_timeout(400)  # the note fades in
        self.assertEqual(page.evaluate("getComputedStyle(document.activeElement.querySelector('.tip')).opacity"), "1")

    def test_takings_accept_k_shorthand(self):
        page = self.new_page()
        self.go(page, "/")
        self.decline_cookies(page)
        page.fill('#compare [data-input="volume-text"]', "12.5k")
        self.calc_result(page)
        self.assertIn("£12,500", page.inner_text("#compare [data-out='shelf-sub']"))

    def test_thank_you_page_recaps_estimate_and_next_steps(self):
        page = self.new_page("phone")
        self.go(page, "/")
        page.evaluate("sessionStorage.setItem('cmd-last-saving', '104.6')")
        self.go(page, "/quote/thanks")
        text = page.inner_text("main")
        self.assertIn("save about £105 a month", text)
        self.assertIn("within 24 hours", text)
        self.assertIn("What happens next", text)
        fresh = self.new_page("phone")
        self.go(fresh, "/quote/thanks")
        self.assertFalse(fresh.is_visible("[data-thanks-estimate]"), "no estimate shown if there wasn't one")

    def test_page_weight_and_speed_on_mobile_4g(self):
        for path in ("/", "/guides/card-machine-fees", "/quote"):
            page = self.new_page("phone")
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 85,
                                                          "downloadThroughput": 9e6 / 8, "uploadThroughput": 1.5e6 / 8})
            sizes = []
            cdp.on("Network.loadingFinished", lambda e: sizes.append(e["encodedDataLength"]))
            self.go(page, path)
            lcp = page.evaluate("""() => new Promise(r => { let v = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) v = e.startTime; })
                .observe({type: 'largest-contentful-paint', buffered: true}); setTimeout(() => r(v), 500); })""")
            with self.subTest(path=path):
                self.assertLess(lcp, 2500, "Largest Contentful Paint over Google's 2.5s 'good' line")
                self.assertLess(sum(sizes), 250_000, "page weight over 250KB")
                self.assertLess(len(sizes), 15, "too many requests")

    def test_fonts_are_self_hosted(self):
        page = self.new_page()
        hosts = []
        page.on("request", lambda r: hosts.append(r.url.split("/")[2]))
        self.go(page, "/")
        self.assertEqual({h for h in hosts if not h.startswith("127.0.0.1")}, set(), "page called a third party before consent")
        self.assertTrue(page.evaluate("document.fonts.check('700 16px \"Work Sans\"')"))


class ConsentTest(BrowserCase):
    def test_declining_keeps_marketing_code_off(self):
        page = self.new_page()
        self.go(page, "/")
        self.assertTrue(page.is_visible("#consent"))
        self.assertFalse(page.evaluate("!!window.__marketingLoaded"), "marketing code ran before consent")
        self.decline_cookies(page)
        self.assertFalse(page.is_visible("#consent"))
        self.go(page, "/guides/")
        self.assertFalse(page.is_visible("#consent"), "banner should remember the choice")
        self.assertFalse(page.evaluate("!!window.__marketingLoaded"))

    def test_accepting_loads_marketing_code_on_every_page(self):
        page = self.new_page()
        self.go(page, "/")
        self.accept_cookies(page)
        self.assertTrue(page.evaluate("!!window.__marketingLoaded"))
        self.go(page, "/terms")
        self.assertTrue(page.evaluate("!!window.__marketingLoaded"))
        self.assertTrue(page.evaluate("dataLayer.some(e => e.event === 'cmd_consent')"))

    def test_strict_mode_counts_nothing_until_consent(self):
        site.app.config["ANALYTICS_CONSENT_REQUIRED"] = True
        self.addCleanup(site.app.config.__setitem__, "ANALYTICS_CONSENT_REQUIRED", False)
        page = self.new_page()
        sent = []
        page.on("request", lambda r: sent.append(r.url) if "/api/events" in r.url else None)
        self.go(page, "/")
        page.evaluate("for (let i = 0; i < 25; i++) window.cmdTrack('test_event', null)")  # would normally force a send
        page.wait_for_timeout(300)
        self.assertEqual(sent, [], "events sent before consent")
        self.assertIsNone(page.evaluate("localStorage.getItem('cmd-vid')"), "visitor ID stored before consent")
        self.assertIn("count your visits", page.inner_text("#consent"))
        self.accept_cookies(page)
        page.wait_for_timeout(500)
        self.assertTrue(sent, "events should be sent once the visitor agrees")
        self.assertIsNotNone(page.evaluate("localStorage.getItem('cmd-vid')"))

    def test_google_analytics_waits_for_consent_then_gets_key_events(self):
        site.app.config["GA4_ID"] = "G-TEST123"
        self.addCleanup(site.app.config.__setitem__, "GA4_ID", "")
        page = self.new_page()
        google = []
        # Never call the real Google: record and block the request instead.
        page.route("https://www.googletagmanager.com/**", lambda r: google.append(r.request.url) or r.abort())
        self.go(page, "/")
        self.assertEqual(google, [], "Google tag loaded before consent")
        self.assertFalse(page.evaluate("typeof window.gtag === 'function'"))
        self.accept_cookies(page)
        page.wait_for_timeout(300)
        self.assertTrue(any("id=G-TEST123" in u for u in google), "Google tag should load after consent")
        page.evaluate("""() => { const s = document.querySelector('#compare [data-input="volume-slider"]');
            s.value = 500; s.dispatchEvent(new Event('input', {bubbles: true})); }""")
        self.calc_result(page)
        sent = page.evaluate("dataLayer.filter(e => e && e[0] === 'event').map(e => e[1])")
        self.assertIn("cmd_calc_result", sent)
        self.assertEqual(page.evaluate("dataLayer.filter(e => e && e[0] === 'config').length"), 1, "exactly one Google tag")

    def test_campaign_memory_needs_consent_but_visit_stats_do_not(self):
        page = self.new_page()
        self.go(page, "/?utm_source=emailblaster&utm_medium=email&utm_campaign=consent-check")
        self.assertIn("opt out", page.inner_text("#consent"))
        self.assertIsNone(page.evaluate("localStorage.getItem('cmd-touch')"), "30-day campaign memory stored before consent")
        attrib = json.loads(page.evaluate("sessionStorage.getItem('cmd-attrib')"))
        self.assertEqual(attrib["campaign"], "consent-check", "this visit is still credited to the campaign")
        self.accept_cookies(page)
        touch = json.loads(page.evaluate("localStorage.getItem('cmd-touch')"))
        self.assertEqual(touch["campaign"], "consent-check")
        page.context.clear_cookies()
        other = self.new_page()
        self.go(other, "/?utm_source=emailblaster&utm_medium=email&utm_campaign=declined")
        self.decline_cookies(other)
        self.assertIsNone(other.evaluate("localStorage.getItem('cmd-touch')"), "stored after declining")

    def test_automated_browser_visits_are_marked_internal(self):
        page = self.new_page()
        self.go(page, "/?utm_source=automation-check")
        self.decline_cookies(page)
        page.evaluate("for (let i = 0; i < 25; i++) window.cmdTrack('test_event', null)")  # force a send
        page.wait_for_timeout(500)
        sid = page.evaluate("sessionStorage.getItem('cmd-sid')")
        with site.store.conn() as c:
            row = c.execute("SELECT internal, internal_reason FROM sessions WHERE session_id=?", (sid,)).fetchone()
        self.assertEqual((row["internal"], row["internal_reason"]), (1, "automated browser"))

    def test_phone_banner_waits_for_first_scroll(self):
        page = self.new_page("phone")
        self.go(page, "/")
        self.assertFalse(page.is_visible("#consent"), "banner covers the calculator result on first view")
        self.assertFalse(page.evaluate("!!window.__marketingLoaded"), "nothing needing consent loads meanwhile")
        page.mouse.wheel(0, 200)
        page.wait_for_selector("#consent", state="visible")
        desktop = self.new_page("desktop")
        self.go(desktop, "/")
        self.assertTrue(desktop.is_visible("#consent"), "desktop shows it straight away")

    def test_privacy_opt_out_stops_counting(self):
        page = self.new_page()
        self.go(page, "/privacy")
        self.decline_cookies(page)
        page.click("[data-optout]")
        sent = []
        page.on("request", lambda r: sent.append(r.url) if "/api/events" in r.url else None)
        self.go(page, "/")
        page.evaluate("for (let i = 0; i < 25; i++) window.cmdTrack('test_event', null)")
        page.wait_for_timeout(300)
        self.assertEqual(sent, [])


class JourneyTest(BrowserCase):
    def test_email_campaign_visitor_gets_a_quote_with_statement(self):
        page = self.new_page("phone")
        self.go(page, "/?utm_source=emailblaster&utm_medium=email&utm_campaign=e2e-test")
        self.accept_cookies(page)
        self.assertTrue(page.evaluate("!!window.__marketingLoaded"), "EmailBlaster code loads after consent")

        # Move the main slider: a loading state shows, then a saving headline.
        page.locator("#compare").scroll_into_view_if_needed()
        page.evaluate("""() => { const s = document.querySelector('#compare [data-input="volume-slider"]');
            s.value = 600; s.dispatchEvent(new Event('input', {bubbles: true})); }""")
        self.assertTrue(page.locator("[data-results].is-loading").count() > 0, "loading state shows")
        rows = self.calc_result(page)
        self.assertIn("Card Machines Direct", rows)
        self.assertNotIn("Dojo", rows)
        self.assertRegex(page.inner_text("#compare [data-headline]"), r"save up to")

        # The brief's worked example: £10,000, £20 average, 1% / 2% / 1p / £10 → £128.00.
        page.fill('#compare [data-input="volume-text"]', "10000")
        page.click("#compare details.more >> nth=0")  # average sale panel
        page.fill('#compare [data-input="atv-text"]', "20")
        page.click("#compare details.more >> nth=1")  # what you pay now
        for sel, v in (("#cur-debit", "1"), ("#cur-credit", "2"), ("#cur-auth", "1"), ("#cur-monthly", "10")):
            page.fill(sel, v)
            page.wait_for_timeout(150)  # type at a person's pace, so earlier requests are still in flight
        rows = self.calc_result(page)
        self.assertIn("£128.00", rows)
        self.assertIn("save about", page.inner_text("#compare [data-headline]"))

        # Through to the quote form, details carried over.
        page.click("#compare [data-quote-link]")
        page.wait_for_url("**/quote**")
        self.assertEqual(page.input_value("#f-monthly_volume"), "10000")
        page.fill("#f-name", "E2E Tester")
        page.fill("#f-business", "E2E Takeaway")
        page.fill("#f-email", "e2e@example.com")
        page.check('input[name="knows_fees"][value="yes"]', force=True)
        self.assertEqual(page.input_value("#f-monthly_fee"), "10")
        page.set_input_files("#f-statement", {"name": "statement.pdf", "mimeType": "application/pdf", "buffer": PDF})
        page.click("button[type=submit]")
        page.wait_for_url("**/quote/thanks")
        self.assertEqual(page.errors, [])

        lead = next(l for l in site.store.leads(0) if l["business"] == "E2E Takeaway")
        self.assertEqual((lead["source"], lead["medium"], lead["campaign"]), ("emailblaster", "email", "e2e-test"))
        self.assertEqual(json.loads(lead["estimate"])["current_monthly"], 128.0)
        self.assertTrue((site.UPLOADS / lead["statement_file"]).exists())
        page.wait_for_timeout(300)
        with site.store.conn() as c:
            s = c.execute("SELECT * FROM sessions WHERE session_id=?", (lead["session_id"],)).fetchone()
        self.assertEqual((s["source"], s["calc_used"], s["form_submitted"]), ("emailblaster", 1, 1))

    def test_slow_answers_never_overwrite_newer_input(self):
        """A late answer for an old amount must never be shown, or clear the loading state, after newer input."""
        page = self.new_page()
        self.go(page, "/")
        self.decline_cookies(page)
        held = []
        page.route("**/api/estimate", lambda route: held.append(route) if not held else route.continue_())
        page.fill('#compare [data-input="volume-text"]', "3000")
        page.wait_for_timeout(600)                                   # request for £3,000 sent, and held back
        self.assertEqual(len(held), 1)
        page.fill('#compare [data-input="volume-text"]', "20000")    # visitor keeps typing...
        held[0].continue_()                                          # ...and the stale £3,000 answer lands now,
        page.wait_for_timeout(120)                                   # before the £20,000 request has even gone
        state = page.evaluate("""() => ({loading: document.querySelector('[data-results]').classList.contains('is-loading'),
                                         shown: document.querySelector("#compare [data-out='shelf-sub']").textContent})""")
        self.assertFalse(not state["loading"] and "£3,000" in state["shown"], "stale result shown as if final")
        self.calc_result(page)
        self.assertIn("£20,000", page.inner_text("#compare [data-out='shelf-sub']"))

    def test_visitor_we_cannot_beat_is_told_honestly(self):
        page = self.new_page()
        self.go(page, "/")
        self.decline_cookies(page)
        page.click("#compare details.more >> nth=1")
        for sel, v in (("#cur-debit", "0.2"), ("#cur-credit", "0.3"), ("#cur-auth", "0"), ("#cur-monthly", "0")):
            page.fill(sel, v)
        self.calc_result(page)
        self.assertIn("can't beat that", page.inner_text("#compare [data-headline]"))
        self.assertTrue(page.is_visible(".no-save-note"), "hero says so too")
        self.assertFalse(page.is_visible(".hero-strip .save-tag"), "no '£0 saving' tag")

    def test_guide_reader_from_google_reaches_the_calculator(self):
        page = self.new_page()
        self.go(page, "/guides/how-to-read-a-merchant-statement", referer="https://www.google.co.uk/")
        self.decline_cookies(page)
        self.assertTrue(page.is_visible("[data-toc]"), "contents list on long guides")
        page.click('.related a >> nth=0')  # "Keep reading"
        page.wait_for_load_state("networkidle")
        self.assertIn("/guides/", page.url)
        page.click('[data-cta="guide-aside"]')
        page.wait_for_url("**/#compare")
        self.assertIn("Card Machines Direct", self.calc_result(page))
        attrib = json.loads(page.evaluate("sessionStorage.getItem('cmd-attrib')"))
        self.assertEqual((attrib["source"], attrib["medium"]), ("google.co.uk", "organic"))

    def test_form_validation_in_the_browser(self):
        page = self.new_page("phone")
        posts = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/quote") else None)
        self.go(page, "/quote")
        self.decline_cookies(page)
        page.click("button[type=submit]")
        summary = page.inner_text("#error-summary")
        for msg in ("Tell us your name", "Add your business name", "That email doesn't look right", "Pick one"):
            self.assertIn(msg, summary)
        self.assertEqual(page.evaluate("document.activeElement.id"), "error-summary", "focus moves to the summary")
        for href in page.eval_on_selector_all("#error-summary a", "els => els.map(a => a.getAttribute('href'))"):
            self.assertEqual(page.locator(href).count(), 1, f"summary link {href} has no target")
        self.assertEqual(posts, [], "nothing sent while there are errors")
        self.assertEqual(page.get_attribute("#f-email", "aria-invalid"), "true")
        page.fill("#f-email", "not an email")
        self.assertTrue(page.is_visible("#email-err"))
        page.fill("#f-email", "ok@example.com")
        self.assertFalse(page.is_visible("#email-err"), "message clears once fixed")
        page.set_input_files("#f-statement", {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"})
        self.assertIn("Upload a PDF or a photo", page.inner_text("#statement-err"))

    def test_server_still_rejects_bad_input_without_javascript(self):
        page = self.new_page(java_script_enabled=False)
        self.go(page, "/quote")
        page.fill("#f-name", "No JS")
        page.fill("#f-business", "No JS Ltd")
        page.fill("#f-email", "wrong")
        page.click("button[type=submit]")
        self.assertIn("That email doesn't look right", page.inner_text("#error-summary"))
        self.assertEqual(page.input_value("#f-name"), "No JS", "typed values are kept")


if __name__ == "__main__":
    unittest.main()
