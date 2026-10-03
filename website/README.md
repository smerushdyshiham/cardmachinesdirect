# Card Machines Direct website

A small Flask app. Pages are rendered on the server, so search engines can read them. The savings calculator does its maths on the server, so confidential partner rates never reach the browser.

## Run it locally

```
pip install -r requirements.txt
set ADMIN_PASSWORD=choose-something        # PowerShell: $env:ADMIN_PASSWORD="choose-something"
python app.py                              # http://127.0.0.1:5000
```

## Tests

Run these before every release:

```
pip install -r requirements-dev.txt
python -m playwright install chromium      # once, about 150MB
python -m unittest discover -s tests       # about 2 minutes
```

- `tests/test_calculator.py`: the fee maths, including the brief's £128 worked example.
- `tests/test_site.py`, server-level checks:
  - no partner rate or secret appears in any page, file or API response;
  - HTTPS redirect, security headers and compression;
  - spam protection and form handling;
  - analytics can't be forged, and old statements are deleted;
  - unique titles and descriptions, alt text, and every internal link and anchor works.
- `tests/test_browser.py`, a real Chromium browser:
  - every page at desktop, tablet and phone widths, with no console errors and no sideways scrolling;
  - an accessibility audit (axe, WCAG 2.1 AA), including colour contrast;
  - keyboard-only use;
  - page speed on mobile 4G;
  - cookie consent;
  - the full journeys: email visitor → calculator → quote with statement → admin, a guide reader from Google, the "we can't beat it" case, and form validation with and without JavaScript.

If Chromium isn't installed, the browser tests are skipped rather than failed.

## Deploy

The live site runs on **Render**, set up from `render.yaml` at the repo root:
- Starter instance in Frankfurt, with a 1GB disk at `/var/data`;
- deploys automatically once a change on `main` has passed the GitHub "Tests" check.

**Set by hand in the Render dashboard, never in the repo:**

- **Secret Files** (Environment → Secret Files):
  - `partners.json`: the confidential partner rates, in the same format as `tests/fixtures/partners.example.json`;
  - `head.html` / `body_end.html` (optional): EmailBlaster tracking code.
- **Environment:**
  - `ADMIN_PASSWORD`;
  - `LEADS_EMAIL` and the `SMTP_*` settings for lead alerts.

After the first deploy, open `/admin/diagnostics`. It should show `is_secure: true`, `partners_file_found: true` and your own IP address as `client_ip_used_for_limits`. If the IP shown is Render's rather than yours, set `CLIENT_IP_HEADER` to whichever header in that page holds your real IP.

Anywhere else (a VPS, Railway): `pip install -r requirements.txt`, then `gunicorn app:app --workers 2 --threads 4 --bind 0.0.0.0:$PORT`. The data directory (`INSTANCE_DIR`) must be on persistent disk, never publicly served, and backed up.

### Environment variables

| Variable | Required | What it does |
| --- | --- | --- |
| `ADMIN_PASSWORD` | Yes | Password for `/admin/`. The admin area is locked if this isn't set. |
| `ADMIN_USER` | No | Admin username. Default `admin`. |
| `SECRET_KEY` | Yes in production | Any long random string. |
| `SITE_URL` | No | Used for canonical links and the sitemap. Default `https://cardmachinesdirect.co.uk`. |
| `GTM_ID` / `GA4_ID` | No | Google Tag Manager or GA4 ID. Loads only after cookie consent. Calculator and form events are pushed to `dataLayer`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `LEADS_EMAIL` | No | Emails you each new quote request. Leads are always saved to the database, even if email fails. |
| `CONTACT_EMAIL` | No | Email address shown on the site. |
| `INSTANCE_DIR` | No | Where `instance/` lives. |
| `PARTNERS_FILE` | No | Path to the partner rates file. Default `INSTANCE_DIR/partners.json`. On Render: `/etc/secrets/partners.json`. |
| `SNIPPETS_DIR` | No | Folder holding `head.html` and `body_end.html` (marketing code). Default `INSTANCE_DIR/snippets`. On Render: `/etc/secrets`. |
| `CLIENT_IP_HEADER` | No | A header your host sets with the visitor's real IP, used for rate limits. Only set it after checking `/admin/diagnostics`. |
| `TRUSTED_PROXIES` | No | How many proxies sit in front of the app. Default `1`. On Render it's `2`, because Cloudflare sits in front of Render's own proxy (confirmed with `/admin/diagnostics`). Use `0` if nothing sits in front. It stops visitors faking their IP to dodge rate limits. |
| `FORCE_HTTPS` | No | Default `1`: plain-HTTP requests are redirected to HTTPS, and browsers are told to stay on HTTPS (HSTS). Local addresses are never redirected. Set to `0` only if your host can't serve HTTPS. |
| `ANALYTICS_CONSENT_REQUIRED` | No | Default `0`: the site's own visit counting runs for everyone, and visitors can opt out on the privacy page. This relies on the UK exemption for analytics. Set to `1` to count visits only after "That's fine". Google Analytics and EmailBlaster always wait for consent either way. |
| `ANALYTICS_RETENTION_DAYS` | No | Visit statistics are deleted after this many days. Default `730`. Shown on the privacy page. |
| `STATEMENT_RETENTION_DAYS` | No | Uploaded statements are deleted automatically after this many days. Default `90`. The privacy page shows the same number. |

## Things you'll change

- **Partner rates:** `instance/partners.json`. Server-only. Never copy these figures into templates, static files or comments. Partners are labelled A/B/C on purpose.
- **Competitor rates:**
  - Stored in `data/competitors.json`. Only providers that publish a standard in-person rate on their own UK pricing page are listed.
  - Re-check every `source` link before each release and update `checked_on`. The site shows that date next to every estimate.
  - Plans with `max_annual_volume` are hidden above that turnover, because the published rate no longer applies.
- **EmailBlaster tracking code:** paste it into `instance/snippets/head.html` and/or `instance/snippets/body_end.html`. See the README in that folder.
- **Campaign links:** add `?utm_source=emailblaster&utm_medium=email&utm_campaign=<name>` so visits and quote requests are credited to the email.
- **Guides:** each guide's text lives in `templates/guides/<slug>.html`. Its title, description and related links live in `guides.py`. When you edit a guide, update `updated` in `guides.py`.

## Analytics setup

1. **Built-in dashboard:** works out of the box at `/admin/`. Nothing to set up.
2. **Email campaigns (EmailBlaster):**
   - paste its tracking code into `instance/snippets/head.html`;
   - tag every link in your emails with `?utm_source=emailblaster&utm_medium=email&utm_campaign=<name>`;
   - the dashboard's **By source** and **Campaigns** tables then show visits and quote requests per email.
3. **Google Analytics 4 (optional):**
   - create a GA4 property and copy its Measurement ID (`G-XXXXXXX`) into `GA4_ID`, or use Tag Manager with `GTM_ID`;
   - it only loads after cookie consent;
   - GA4 receives page views plus these events: `cmd_calc_result` (saw a saving), `cmd_cta_click` (clicked a quote button), `cmd_quote_view`, `cmd_form_start` and `cmd_form_error`. With Tag Manager instead, every site event is available in `dataLayer` as `cmd_<event>`;
   - for conversions, use page views of `/quote/thanks`. Only real submissions reach that page.

## Admin

`/admin/` (password protected) shows:

- **Journeys:** visits, the funnel from visit to quote, results by source and campaign, entry and exit pages, time on each section and page, and a step-by-step view of any single visit.
- **Quote requests:** every lead, with source, the calculator estimate and a download link for any uploaded statement.

## Brand rules that apply to code too

See `../CLAUDE.md`.

- Never name a processing partner anywhere, including comments and sample data.
- Never publish an "X% cheaper than [competitor]" claim.
- Never expose partner rates client-side.
