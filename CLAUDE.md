# Developer Handover — Card Machines Direct

This is the brand and positioning context for whoever builds cardmachinesdirect.co.uk. Functional requirements live in a separate brief from Rushdy — this covers what the site needs to *feel* like and *say*, not what it needs to *do*.

## The job of this site

Convince a UK small business owner, in well under two minutes, that they're probably overpaying to take card payments — and get them to start a comparison.

## Who's landing here

UK small businesses taking card payments in person — takeaways, independent retail, bars, restaurants, hotels, cafes, barbers, phone repair shops. Most don't know their current rate, think they know but are wrong, or know and can still be beaten. Assume low patience for jargon and zero trust in "fintech" marketing on a first visit.

## The brand in one paragraph

Card Machines Direct is a straight-talking challenger, not another faceless processor. We compare the market, match a business to whoever's genuinely cheapest for them, and say so in plain numbers. Confident with numbers, myth-busting about an industry that profits from the small print, never corporate, never condescending.

## Writing for this site

- Real numbers over vague promises — "save about £95/month," not "save more."
- Plain English. Explain any processing term in the same sentence it appears.
- Short sentences, active voice, talk to one business owner, not "valued customers."
- Never oversell, or invent a saving that hasn't actually been calculated.

## Design tokens

| Token | Value | Use |
| --- | --- | --- |
| Ink | `#1A1A1A` | Primary text |
| Accent | `#FF6B35` | CTAs, savings figures |
| Background | `#FAFAF8` | Page background |
| Muted | `#6B6B6B` | Secondary text |
| Typeface | Work Sans (Google Fonts) | Bold for headlines/numbers, regular for body |

Logo: wordmark, "Card Machines" in ink, "Direct" in accent. Concept artifact linked separately.

## If a trade-off comes up, in this order

1. Does it save the customer money, or make pricing clearer?
2. Can it be explained in one plain sentence?
3. Can it ship lean and fast, or does it need real investment first?
4. Does it build trust, or just chase a short-term win?

Earlier wins override later ones.

## Hard constraints — do not cross these

- Never name a processing partner (MyPOS, Clover, Shift4, or any other) anywhere in UI copy, code comments, or sample data. The customer is told they get "the cheapest available option," never which company.
- Never publish a specific "X% cheaper than \[Competitor\]" claim. Competitor rates gathered for strategy are not verified for public claims, and some (Dojo) are explicitly unconfirmed.
- Never hardcode or expose the confidential supplier rates anywhere client-visible, including in comments or placeholder copy.

## For anything not covered here

The full Brand Foundations doc has the complete vision, goals, target-customer detail, and competitive data. The logo concept artifact has the live, editable wordmark. Ask Rushdy for both if they're not already in the project.


---

# Engineering knowledge (kept up to date by Claude)

Everything below records how the site is actually built, run and changed, plus decisions Rushdy has made. Setup steps and every environment variable are in `website/README.md`. Read that before deploying or configuring anything.

## Status (as of 3 Oct 2026)

Live at **https://cardmachinesdirect.co.uk**, on Render, deploying from GitHub. Launch work is complete. Still open:

- solicitor review of the privacy and terms pages;
- Rushdy to confirm the ICO data protection fee is paid;
- monthly re-check of competitor prices.

## Where things live

| What | Where |
| --- | --- |
| Code | GitHub `smerushdyshiham/cardmachinesdirect` (private, Rushdy's personal account). Local working copy: this folder, which is inside OneDrive. |
| App | `website/`: Flask app (`app.py`), fee maths (`calculator.py`), SQLite storage and analytics (`store.py`), guide metadata (`guides.py`), Jinja templates, static files, tests. |
| Hosting | Render web service `cardmachinesdirect`: Starter instance, Frankfurt, 1GB disk at `/var/data`. Configured by `render.yaml` (a Render Blueprint) at the repo root. |
| Confidential partner rates | **Only** in a Render Secret File, `/etc/secrets/partners.json`, plus Rushdy's local `website/instance/partners.json`. Both are git-ignored. Partners are labelled A/B/C, never named. |
| Marketing code | Render Secret File `head.html`: the EmailBlaster Insight script (`https://dujantdza7z0f.cloudfront.net/Insight/Insight.js`). Loaded only after cookie consent. |
| Data on the server | `/var/data/instance`: `site.db` (leads, sessions, events) and `uploads/` (statements). |
| Domain and DNS | `cardmachinesdirect.co.uk` points to Render; `www` and `http` redirect to `https://cardmachinesdirect.co.uk`. Render renews the SSL certificate automatically. |
| Email | Mailboxes are hosted at **JustHost** (MX `just5200.justhost.com`). Cancelling JustHost would kill all `@cardmachinesdirect.co.uk` email. Public contact address: `info@cardmachinesdirect.co.uk`. Lead alerts go via Rushdy's SMTP server on **port 465 (SSL)**, sent on a background thread. |
| Analytics | Built-in dashboard `/admin/` (HTTP basic auth, user `admin`). GA4 `G-NQCB0QHDPY` (set in `render.yaml`). Google Search Console is verified, with the sitemap submitted. UptimeRobot checks `/healthz`. |
| Legal entity | ORDUGH FOODS LTD, trading as Card Machines Direct. Company 15815253, registered in England and Wales. Registered office: 128 City Road, London, United Kingdom, EC1V 2NX. Defined once in `COMPANY` in `app.py`, and shown in the footer, privacy and terms. |

## How a change ships

1. Make a branch. `main` is protected by a GitHub ruleset: no direct pushes, a pull request is required, and the **Tests** check must pass. This was verified by a rejected push.
2. Make the change, then run `python -m unittest discover -s tests` from `website/`. There are 87 tests, about 2 to 3 minutes, including Playwright browser tests (`python -m playwright install chromium` once).
3. Commit, push the branch, and give Rushdy the `pull/new/<branch>` link. There's no `gh` CLI on this machine. Rushdy opens and merges the pull request.
4. GitHub Actions (`.github/workflows/tests.yml`) runs the tests, plus a guard that fails if confidential files or partner names are tracked.
5. Render auto-deploys `main` after the checks pass (`autoDeployTrigger: checksPass`).
   - **Each deploy causes about 30 to 60 seconds of downtime**, because a service with a disk can't do zero-downtime deploys. Don't deploy during email campaigns.
   - If `render.yaml` changed, Rushdy may need to approve a **Blueprint sync** in Render.
6. After a deploy, check the live site with read-only `curl` checks. `/admin/diagnostics` (admin only) shows HTTPS and IP detection, whether the rates file was found, which snippets are loaded, and whether SMTP is configured.

## Calculator rules (the brief, as built)

- **Monthly fee** = (debit volume × debit %) + (credit volume × credit %) + (transactions × auth fee + monthly fee) × 1.2 VAT. Percentage fees are VAT-exempt; VAT applies to the fixed parts only.
- **Inputs:** debit/credit split is fixed at 90/10. Transactions = volume ÷ average sale, with average sale defaulting to £20.
- **Reference example:** £10,000 at £20, 1% debit, 2% credit, 1p auth, £10 a month = **£128**. A test checks this, including via the quote form, which sends `monthly_fee` while the calculator sends `monthly`.
- **Our row** is the cheapest partner for the inputs, labelled "Card Machines Direct / Cheapest available option". It's rounded to whole pounds, and there's **no per-£100 figure**, because that would reveal a flat partner rate. All maths runs server-side (`/api/estimate`); rates never reach the browser.
- **Headline** "save up to £X" compares against the **most expensive** published competitor only (Rushdy's decision). If the visitor enters their own fees, those become the comparison ("save about").
- **When we're not cheaper**, say so plainly ("we can't beat that right now"). No £0 saving tag and no strike-through.
- **Competitors** live in `website/data/competitors.json`. Only providers that publish standard in-person rates on their own UK page are included: SumUp, Square, Zettle, Tyl and Viva.
  - **Dojo is removed for good** (Rushdy's decision).
  - Plans with `max_annual_volume` drop out above that turnover.
  - Update `checked_on` whenever prices are re-checked. The site shows that date.

## Decisions Rushdy has made (don't reopen without being asked)

- Stack: Flask on Render (Starter plus disk; the Free tier was rejected because it sleeps and has no disk). Visual direction: the "shelf-edge price label".
- Our price showing a flat partner rate on screen (e.g. £70 on £10k) is accepted as unavoidable.
- Never name our provider. Do name competitors.
- No "vs cheapest published price" line, only the headline against the most expensive competitor.
- Statement uploads are deleted after 90 days; visit statistics after 730 days.
- The test lead "TEST Card Machines Direct" stays in the live database on purpose.
- **Consent** (Rushdy delegated the decision):
  - first-party visit counting runs without consent, under the UK statistics exemption (Data (Use and Access) Act 2025 s.123, in force 5 Feb 2026), with an opt-out link in the banner and on the privacy page;
  - GA4, EmailBlaster and the 30-day campaign memory (`cmd-touch`) only run after "That's fine";
  - `ANALYTICS_CONSENT_REQUIRED=1` switches to fully consent-first if ever needed.

## UX rules (from the laws-of-UX review, 6 Oct 2026)

- **First screen:** at every size from a 320 × 568 phone to desktop (tablets and 1280 × 720 / 1024 × 768 laptops included), the saving tag and the hero **Get my exact quote** button must be visible without scrolling and not under the cookie banner. Tests enforce this at 8 sizes. (Unusually short windows, under about 700px tall on a laptop, still show the saving; the button may be partly below.)
- **Cookie banner:** on every screen size it waits for the first scroll, or 20 seconds, so it never covers the result or the quote button. Nothing that needs consent loads before an answer. On 320px-wide short screens the struck-through competitor price line is hidden, so the hero fits.
- **Labels:** exactly two, everywhere. **Compare my fees** (calculator) and **Get my exact quote** (quote form; "Get my quote" in the header on phones). The form's own button is "Send for my quote".
- **Tap targets:** every control is at least 44px tall. Competitor notes in the chart open on tap or focus, not just hover.
- **Input:** amounts accept "£10,000", "10k", "£10.5k", "1.2%" and "4p" (`parse_number` in `app.py`, `parseMoney` in `calculator.js`).
- **Thank-you page:** recaps the visitor's estimate, lists 3 next steps, and promises a reply **within 24 hours** (Rushdy's commitment). There's deliberately **no** confirmation email to the customer.
- **Google reviews:**
  - `/review?from=<channel>` redirects to the Google review page and is counted per channel. Use it, not the raw Google link, everywhere.
  - The ask is a homepage section ("Good or bad, we want to hear it", so there's no review gating) plus a footer link. It's deliberately **not** on the thank-you page, because those people aren't customers yet.
  - No review schema or star ratings on the site, and never invented reviews.
- **Calculator timing:** about 850ms from input to result (280ms debounce plus a 450ms minimum loading state). Rushdy chose to keep it rather than speed it up to under 400ms.

## Security and robustness already in place

- **HTTPS:** forced with HSTS. `/healthz` is exempt, because Render checks it over plain HTTP. `TRUSTED_PROXIES=2`, because Cloudflare sits in front of Render's proxy (visitor → Cloudflare → Render → app). With 1, every visitor shared one IP and one rate limit.
- **Spam:**
  - honeypot field;
  - signed form token (forms with no token, or sent in under 3 seconds, are dropped silently);
  - 5 quote posts per 10 minutes per IP;
  - upload magic-byte checks, 10MB limit.
- **Internal traffic:** visits and leads have an `internal` flag. Opening `/admin/` sets a `cmd_internal` cookie marking that browser as Rushdy's; automated browsers (`navigator.webdriver`, which includes Claude's Playwright checks) and optional `INTERNAL_IPS` are marked too. The admin shows real visitors by default (Real / Internal / Everyone filter) and has mark/unmark buttons. Internal lead emails start with "[Internal]".
- **Analytics integrity:** `form_submit` can only be recorded server-side, not by visitors. The events endpoint tolerates malformed payloads and concurrent first requests.
- **Secret key:** if `SECRET_KEY` isn't set, the app generates one into `instance/` so all workers agree. On Render, `SECRET_KEY` is generated by the Blueprint.
- **Assets:** CSS/JS are versioned by file modification time (`asset()` helper), so the one-week cache never serves stale files. Responses are gzipped in-app.
- **Performance and accessibility:** the font is self-hosted (`static/fonts/`). axe-core accessibility tests run in CI. The only accepted contrast exception is the orange "Direct" in the logo.

## Gotchas learned the hard way

- **Brand Foundations doc must never be committed:** it names the partners and lists their rates. It once went into a commit and the repo had to be deleted and recreated. It's now in `.gitignore`, and the CI guard catches it.
- **Writing files from this shell:**
  - backslashes and apostrophes in bash heredocs get mangled. For any edit containing `\n`, regex or quotes, write a Python script to the scratchpad with the Write tool and run it;
  - some files (`track.js`, `README.md`) have CRLF line endings. Preserve them, or the diff shows every line changed.
- **Local preview server:**
  - the Flask server caches templates; restart it after template edits;
  - gunicorn doesn't run on Windows, so to simulate Render, run the app with the same env vars (see README);
  - background shell tasks get killed after a time limit, so restart the demo server if the user asks again.
- **"Tag not detected" in GA4 is expected:** the tag only loads after consent, so Google's crawler never sees it. Verify with real requests to `google-analytics.com/g/collect` instead.
- **Timing in browser tests:** they once caught a real race (a stale calculator answer overwriting newer input). `schedule()` now bumps `seq` so in-flight answers are discarded. Keep the regression test.
- **Facts in the guides** (FCA contactless change from 19 Mar 2026, High Court ruling of 15 Jan 2026, PSR PS26/1 of 30 Jul 2026) were verified on 3 Oct 2026. Re-verify anything regulatory before relying on it, and cite the regulator's own page.
