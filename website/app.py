"""Card Machines Direct website."""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import re
import secrets
import smtplib
import ssl
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from markupsafe import Markup
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix

from calculator import Estimator, parse_current
from guides import BY_SLUG, GUIDES
from store import Store

BASE = Path(__file__).resolve().parent
INSTANCE = Path(os.environ.get("INSTANCE_DIR", BASE / "instance"))
UPLOADS = INSTANCE / "uploads"
# On Render these come from "Secret Files" (/etc/secrets/...), so the confidential rates and the marketing
# code are managed in the dashboard and never live in the repo or on the data disk.
SNIPPETS = Path(os.environ.get("SNIPPETS_DIR", INSTANCE / "snippets"))
PARTNERS_FILE = Path(os.environ.get("PARTNERS_FILE", INSTANCE / "partners.json"))

# The legal entity behind the site. Companies Act rules require these on the website.
COMPANY = {
    "name": "ORDUGH FOODS LTD",
    "number": "15815253",
    "registered_in": "England and Wales",
    "address": "128 City Road, London, United Kingdom, EC1V 2NX",
}

log = logging.getLogger("cmd")
if not logging.getLogger().handlers:  # under gunicorn nothing is configured, so our messages would never reach the host's logs
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__, instance_path=str(INSTANCE))
# Trust only as many X-Forwarded-For hops as there are real proxies (1 on Render/Railway), so visitors can't spoof their IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=int(os.environ.get("TRUSTED_PROXIES", "1")), x_proto=1)


def _secret_key() -> str:
    """SECRET_KEY from the environment, else one generated once and kept in instance/, so every worker
    and every restart signs form tokens the same way."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = INSTANCE / "secret_key"
    INSTANCE.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    FORCE_HTTPS=os.environ.get("FORCE_HTTPS", "1") != "0",
    ANALYTICS_CONSENT_REQUIRED=os.environ.get("ANALYTICS_CONSENT_REQUIRED", "0") == "1",
    MAX_CONTENT_LENGTH=12 * 1024 * 1024,
    SITE_URL=os.environ.get("SITE_URL", "https://cardmachinesdirect.co.uk").rstrip("/"),
    GTM_ID=os.environ.get("GTM_ID", ""),
    GA4_ID=os.environ.get("GA4_ID", ""),
    CONTACT_EMAIL=os.environ.get("CONTACT_EMAIL", "info@cardmachinesdirect.co.uk"),
)

if not PARTNERS_FILE.exists():
    raise SystemExit(f"Partner rates file not found at {PARTNERS_FILE}. Set PARTNERS_FILE or add the Secret File.")
estimator = Estimator(PARTNERS_FILE, BASE / "data" / "competitors.json")
store = Store(INSTANCE / "site.db")
UPLOADS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- templates --
def _snippet(name: str) -> Markup:
    """Raw HTML from instance/snippets, e.g. the EmailBlaster tracking code."""
    path = SNIPPETS / name
    try:
        return Markup(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Markup("")


@app.context_processor
def inject_globals():
    return {
        "guides": GUIDES,
        "site_url": app.config["SITE_URL"],
        "gtm_id": app.config["GTM_ID"],
        "ga4_id": app.config["GA4_ID"],
        "contact_email": app.config["CONTACT_EMAIL"],
        "company": COMPANY,
        "analytics_consent_required": app.config["ANALYTICS_CONSENT_REQUIRED"],
        "marketing_head": _snippet("head.html"),
        "marketing_body": _snippet("body_end.html"),
        "rates_checked_on": _pretty_date(estimator.checked_on),
        "year": date.today().year,
        "canonical": app.config["SITE_URL"] + request.path,
    }


@app.template_global()
def asset(filename: str) -> str:
    """Static URL with a file-modified stamp, so the week-long cache never serves stale CSS/JS."""
    try:
        v = int((BASE / "static" / filename).stat().st_mtime)
    except OSError:
        v = 0
    return url_for("static", filename=filename, v=v)


def _pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


@app.template_filter("gbp")
def gbp(value, pence=True):
    if value is None:
        return "–"
    return f"£{value:,.2f}" if pence else f"£{value:,.0f}"


@app.template_filter("from_json")
def from_json(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


@app.template_filter("ago")
def ago(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %H:%M")


LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


@app.before_request
def force_https():
    """Send every plain-HTTP request to HTTPS (behind the host's proxy, which sets X-Forwarded-Proto)."""
    if (not app.config["FORCE_HTTPS"] or request.is_secure or request.host.split(":")[0] in LOCAL_HOSTS
            or request.path == "/healthz"):  # the host's internal health checks come in over plain HTTP
        return None
    # 308 keeps the method and body, so a form posted over HTTP isn't silently turned into a GET.
    return redirect(request.url.replace("http://", "https://", 1), 301 if request.method in ("GET", "HEAD") else 308)


COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml",
                "application/manifest+json")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=604800")
    elif request.path.startswith(("/admin", "/api/", "/quote")):
        resp.headers.setdefault("Cache-Control", "no-store")
    return _gzip(resp)


def _gzip(resp):
    """Compress text responses. Most hosts don't do this for you, and it cuts CSS/HTML transfer by ~75%."""
    if (resp.status_code != 200 or "gzip" not in request.headers.get("Accept-Encoding", "")
            or "Content-Encoding" in resp.headers or not (resp.mimetype or "").startswith(COMPRESSIBLE)):
        return resp
    source = resp.response  # for static files this is an open file wrapper; it must be closed once read
    resp.direct_passthrough = False
    data = resp.get_data()
    if hasattr(source, "close"):
        source.close()
    if len(data) < 1024:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


# -------------------------------------------------------------------- pages --
@app.get("/")
def home():
    return render_template("home.html", est=estimator.estimate(10_000, 20))


@app.get("/guides/")
def guides_index():
    return render_template("guides_index.html")


@app.get("/guides/<slug>")
def guide(slug):
    meta = BY_SLUG.get(slug)
    if not meta:
        abort(404)
    return render_template(f"guides/{slug}.html", guide=meta)


@app.get("/guides")
def guides_redirect():
    return redirect(url_for("guides_index"), 301)


@app.get("/privacy")
def privacy():
    return render_template("privacy.html", retention_days=STATEMENT_RETENTION_DAYS,
                           analytics_days=ANALYTICS_RETENTION_DAYS)


@app.get("/healthz")
def healthz():
    """For the host's health checks: the app is up and the database answers."""
    with store.conn() as c:
        c.execute("SELECT 1").fetchone()
    return Response("ok", mimetype="text/plain", headers={"Cache-Control": "no-store"})


@app.get("/terms")
def terms():
    return render_template("terms.html")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(BASE / "static", "favicon.ico", max_age=604800)


@app.get("/how-we-make-money")
def how_we_make_money():
    return render_template("how_we_make_money.html")


@app.route("/quote", methods=["GET", "POST"])
def quote():
    if request.method == "GET":
        return render_template("quote.html", errors={}, values=request.args, form_token=_form_token())

    form = request.form
    # Spam checks, cheapest first. Bots are sent to the thank-you page so they learn nothing.
    if form.get("website"):  # honeypot: real people never see this field
        return redirect(url_for("quote_thanks"))
    if _too_many("quote-post", limit=5, per=600):
        return render_template("quote.html", values=form, form_token=_form_token(),
                               errors={"form": "You've sent a few requests in a short time. Wait ten minutes and try again, or email us."}), 429
    started = _form_started(form.get("ft"))
    if started == "expired":
        return render_template("quote.html", values=form, form_token=_form_token(),
                               errors={"form": "This page was open for a long time, so we need you to send it once more."}), 422
    if started is None or time.time() - started < MIN_FORM_SECONDS:
        log.info("Quote dropped as likely spam (token %s)", "missing/invalid" if started is None else "too fast")
        return redirect(url_for("quote_thanks"))

    errors, values = _validate_quote(form)
    upload = request.files.get("statement")
    stored_name = original_name = None
    if upload and upload.filename:
        stored_name, err = _save_statement(upload)
        if err:
            errors["statement"] = err
        else:
            original_name = upload.filename[:200]

    if errors:
        if stored_name:
            (UPLOADS / stored_name).unlink(missing_ok=True)
        return render_template("quote.html", errors=errors, values=form, form_token=form.get("ft")), 422

    estimate = None
    try:
        volume = float(values.get("monthly_volume") or 0)
        if volume:
            try:
                atv = float(form.get("atv") or 20)  # average sale carried over from the calculator
                atv = min(max(atv, 1.0), 2000.0) if atv == atv else 20.0  # atv == atv rejects NaN
            except (ValueError, OverflowError):
                atv = 20.0
            estimate = estimator.estimate(volume, atv, parse_current(form))
    except ValueError:
        pass

    attrib = _attrib(form)
    lead_id = store.add_lead({
        **values,
        "statement_file": stored_name,
        "statement_name": original_name,
        "session_id": form.get("sid", "")[:64],
        "visitor_id": form.get("vid", "")[:64],
        **attrib,
        "estimate": json.dumps(_estimate_summary(estimate)) if estimate else None,
    })
    if form.get("sid"):
        store.record({"sid": form["sid"][:64], "vid": form.get("vid"), "page": "/quote", "attrib": attrib,
                      "events": [{"type": "form_submit", "page": "/quote", "data": {"lead": lead_id}}]},
                     request.headers.get("User-Agent", ""), trusted=True)
    _notify(lead_id, values, original_name, attrib)
    return redirect(url_for("quote_thanks"))


@app.get("/quote/thanks")
def quote_thanks():
    return render_template("thanks.html")


# ------------------------------------------------------------- spam checks --
MIN_FORM_SECONDS = 3  # nobody fills in the quote form faster than this
_signer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="quote-form")


def _form_token() -> str:
    return _signer.dumps(time.time())


def _form_started(token):
    """When the form was served: a timestamp, "expired" after two days, or None if missing or forged."""
    if not token:
        return None
    try:
        return float(_signer.loads(token, max_age=2 * 86400))
    except SignatureExpired:
        return "expired"
    except (BadSignature, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- api --
_hits: dict[str, deque] = defaultdict(deque)


def client_ip() -> str:
    """The visitor's IP for rate limiting. CLIENT_IP_HEADER names a header the host sets itself and visitors
    can't fake (check /admin/diagnostics after deploying); otherwise the proxy-corrected address."""
    header = os.environ.get("CLIENT_IP_HEADER")
    return (request.headers.get(header) if header else None) or request.remote_addr or "unknown"


def _too_many(name: str, limit: int, per: float) -> bool:
    """Sliding-window rate limit per client IP (in memory, per worker)."""
    if not app.config.get("RATE_LIMITS", True):  # switched off only by the browser test suite
        return False
    now = time.time()
    if len(_hits) > 20000:  # forget idle clients so memory can't grow without bound
        for k in [k for k, q in _hits.items() if not q or q[-1] < now - 3600]:
            del _hits[k]
    q = _hits[f"{name}:{client_ip()}"]
    while q and q[0] < now - per:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def rate_limited(limit: int, per: float):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if _too_many(fn.__name__, limit, per):
                return jsonify(error="Too many requests. Give it a minute."), 429
            return fn(*a, **kw)
        return wrapper
    return deco


@app.post("/api/estimate")
@rate_limited(90, 60)
def api_estimate():
    data = request.get_json(silent=True) or {}
    try:
        volume = float(data.get("volume", 0))
        atv = float(data.get("atv", 20) or 20)
    except (TypeError, ValueError):
        return jsonify(error="Enter your monthly card takings as a number."), 400
    if not (math.isfinite(volume) and math.isfinite(atv)) or volume <= 0 or atv <= 0:
        return jsonify(error="Enter your monthly card takings as a number."), 400
    return jsonify(estimator.estimate(volume, atv, parse_current(data.get("current") or {})))


@app.post("/api/events")
@rate_limited(240, 60)
def api_events():
    raw = request.get_data(cache=False, as_text=True)[:20000]
    try:
        payload = json.loads(raw)
    except ValueError:
        return "", 204
    if isinstance(payload, dict):
        store.record(payload, request.headers.get("User-Agent", ""))
    return "", 204


# -------------------------------------------------------------------- admin --
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user, pw = os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_PASSWORD")
        auth = request.authorization
        if not pw or not auth or auth.username != user or not secrets.compare_digest(auth.password or "", pw):
            return Response("Sign in required.", 401, {"WWW-Authenticate": 'Basic realm="CMD admin"'})
        return fn(*a, **kw)
    return wrapper


def _range():
    days = request.args.get("days", "30")
    days = int(days) if days.isdigit() and 0 < int(days) <= 365 else 30
    source = request.args.get("source") or None
    return days, time.time() - days * 86400, source


STATEMENT_RETENTION_DAYS = int(os.environ.get("STATEMENT_RETENTION_DAYS", "90"))
ANALYTICS_RETENTION_DAYS = int(os.environ.get("ANALYTICS_RETENTION_DAYS", "730"))
_last_purge = 0.0


def purge_old_statements(force: bool = False) -> None:
    """Delete uploaded statements past the retention period promised in the privacy notice."""
    global _last_purge
    if not force and time.time() - _last_purge < 3600:
        return
    _last_purge = time.time()
    for name in store.expire_statements(time.time() - STATEMENT_RETENTION_DAYS * 86400):
        (UPLOADS / Path(name).name).unlink(missing_ok=True)
    store.expire_analytics(time.time() - ANALYTICS_RETENTION_DAYS * 86400)


@app.before_request
def _housekeeping():
    if request.endpoint in ("quote", "admin_dashboard", "admin_leads"):
        purge_old_statements()


@app.get("/admin/")
@admin_required
def admin_dashboard():
    days, since, source = _range()
    return render_template("admin/dashboard.html", r=store.report(since, source), days=days, source=source,
                           sessions=store.recent_sessions(since, source, 40))


@app.get("/admin/leads")
@admin_required
def admin_leads():
    days, since, source = _range()
    rows = store.leads(since, source)
    return render_template("admin/leads.html", leads=rows, days=days, source=source,
                           sources=store.report(since)["sources"])


@app.get("/admin/journey/<sid>")
@admin_required
def admin_journey(sid):
    return render_template("admin/journey.html", sid=sid, events=store.journey(sid))


@app.get("/admin/diagnostics")
@admin_required
def admin_diagnostics():
    """What the host's proxy tells us about this request: used once after deploying to check that HTTPS
    and visitor IPs are detected correctly (rate limits depend on it). Shows only your own request."""
    names = ("X-Forwarded-For", "X-Forwarded-Proto", "True-Client-IP", "CF-Connecting-IP", "X-Real-IP", "Host")
    return jsonify(
        remote_addr=request.remote_addr, client_ip_used_for_limits=client_ip(), is_secure=request.is_secure,
        scheme=request.scheme, trusted_proxies=os.environ.get("TRUSTED_PROXIES", "1"),
        client_ip_header=os.environ.get("CLIENT_IP_HEADER"), headers={n: request.headers.get(n) for n in names},
        partners_file_found=PARTNERS_FILE.exists(), snippets=[n for n in ("head.html", "body_end.html") if (SNIPPETS / n).exists()],
        instance_dir=str(INSTANCE), smtp_configured=bool(os.environ.get("SMTP_HOST") and os.environ.get("LEADS_EMAIL")),
    )


@app.get("/admin/statement/<int:lead_id>")
@admin_required
def admin_statement(lead_id):
    lead = store.lead(lead_id)
    if not lead or not lead["statement_file"]:
        abort(404)
    return send_from_directory(UPLOADS, lead["statement_file"], as_attachment=True,
                               download_name=lead["statement_name"] or lead["statement_file"])


# ---------------------------------------------------------------------- seo --
@app.get("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin/\nDisallow: /api/\nSitemap: {app.config['SITE_URL']}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    pages = {"/": "home.html", "/quote": "quote.html", "/guides/": "guides_index.html",
             "/how-we-make-money": "how_we_make_money.html", "/privacy": "privacy.html", "/terms": "terms.html"}
    pages.update({f"/guides/{g['slug']}": f"guides/{g['slug']}.html" for g in GUIDES})

    def lastmod(template):  # when the page's own template last changed
        return date.fromtimestamp((BASE / "templates" / template).stat().st_mtime).isoformat()

    items = "".join(f"<url><loc>{app.config['SITE_URL']}{u}</loc><lastmod>{lastmod(t)}</lastmod></url>"
                    for u, t in pages.items())
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return Response(xml, mimetype="application/xml")


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(_):
    return render_template("500.html"), 500


@app.errorhandler(413)
def too_large(_):
    return render_template("quote.html", errors={"statement": "That file is over 10MB. Try a PDF or a smaller photo."},
                           values=request.form, form_token=_form_token()), 413


# ------------------------------------------------------------------ helpers --
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
KNOWS_FEES = {"yes": "Yes, I know my rates", "roughly": "Roughly", "no": "No idea"}
ALLOWED_UPLOADS = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8",
                   ".webp": b"RIFF", ".heic": None}


def _validate_quote(form):
    values = {k: (form.get(k) or "").strip()[:500] for k in (
        "name", "business", "email", "phone", "business_type", "provider", "knows_fees", "debit_pct",
        "credit_pct", "auth_p", "monthly_fee", "monthly_volume", "message")}
    values["message"] = (form.get("message") or "").strip()[:3000]
    errors = {}
    if not values["name"]:
        errors["name"] = "Tell us your name so we know who to ask for."
    if not values["business"]:
        errors["business"] = "Add your business name."
    if not EMAIL_RE.match(values["email"]):
        errors["email"] = "That email doesn't look right. Check for a typo."
    phone_digits = re.sub(r"\D", "", values["phone"])
    if values["phone"] and not 10 <= len(phone_digits) <= 13:
        errors["phone"] = "Enter a UK phone number, or leave it blank."
    if values["knows_fees"] not in KNOWS_FEES:
        errors["knows_fees"] = "Pick one, even if it's 'no idea'."
    for key in ("debit_pct", "credit_pct", "auth_p", "monthly_fee", "monthly_volume"):
        if values[key]:
            try:
                n = float(values[key].replace(",", "").lstrip("£"))
                if not math.isfinite(n) or n < 0:
                    raise ValueError
                values[key] = values[key].replace(",", "").lstrip("£")
            except ValueError:
                errors[key] = "Numbers only, please."
    return errors, values


def _save_statement(upload):
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_UPLOADS:
        return None, "Upload a PDF or a photo (JPG, PNG, HEIC)."
    head = upload.stream.read(8)
    upload.stream.seek(0)
    magic = ALLOWED_UPLOADS[ext]
    if magic and not head.startswith(magic):
        return None, "That file doesn't look like a real PDF or image."
    name = f"{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(8)}{ext}"
    upload.save(UPLOADS / name)
    return name, None


def _attrib(form):
    try:
        data = json.loads(form.get("attrib") or "{}")
    except ValueError:
        data = {}
    return {
        "source": str(data.get("source") or "direct")[:80],
        "medium": str(data.get("medium") or "none")[:80],
        "campaign": str(data.get("campaign") or "")[:120],
    }


def _estimate_summary(est):
    ours = next(r["monthly"] for r in est["rows"] if r["kind"] == "ours")
    current = next((r["monthly"] for r in est["rows"] if r["kind"] == "current"), None)
    return {"volume": est["volume"], "ours_monthly": ours, "current_monthly": current,
            "saving_monthly": est["saving_monthly"], "basis": est["basis"]}


# Lead alerts go out on a background thread, so the visitor sees the thank-you page straight away
# instead of waiting for the mail server. The lead is already saved before this runs.
_mailer = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lead-mail")


def _notify(lead_id, values, statement_name, attrib):
    if not os.environ.get("SMTP_HOST") or not os.environ.get("LEADS_EMAIL"):
        return None
    msg = _lead_email(lead_id, values, statement_name, attrib)
    return _mailer.submit(_send_email, msg, lead_id)


def _lead_email(lead_id, values, statement_name, attrib) -> EmailMessage:
    to = os.environ["LEADS_EMAIL"]
    msg = EmailMessage()
    business = " ".join(values["business"].split())  # no line breaks: they're not allowed in a subject
    msg["Subject"] = f"New quote request #{lead_id}: {business}"[:200]
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or to
    msg["To"] = to
    lines = [f"{k}: {v}" for k, v in values.items() if v]
    lines += [f"statement: {statement_name or 'none'}", f"source: {attrib['source']} / {attrib['medium']} / {attrib['campaign']}",
              f"View: {app.config['SITE_URL']}/admin/leads"]
    msg.set_content("\n".join(lines))
    return msg


def _send_email(msg: EmailMessage, lead_id) -> bool:
    """SMTP_PORT 465 (or SMTP_SECURITY=ssl) uses SSL from the start; anything else upgrades with STARTTLS."""
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    ssl_mode = os.environ.get("SMTP_SECURITY", "ssl" if port == 465 else "starttls").lower()
    context = ssl.create_default_context()
    try:
        if ssl_mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls(context=context)
        with server as s:
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        log.info("Lead alert sent for lead %s", lead_id)
        return True
    except Exception:  # a failed email must never lose the lead; it's already stored
        log.exception("Lead alert email failed for lead %s (host %s, port %s, %s)", lead_id, host, port, ssl_mode)
        return False

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
