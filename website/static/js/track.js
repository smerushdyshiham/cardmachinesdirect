/* First-party journey analytics + consent-gated marketing code.
 * Sends: page views, time per section, calculator use, CTA clicks, form progress, exits.
 * Every event is also pushed to window.dataLayer for GTM / GA4.
 */
(function () {
  "use strict";

  const ENDPOINT = "/api/events";
  const SESSION_IDLE_MS = 30 * 60 * 1000;
  const page = document.body.dataset.page || location.pathname;

  const store = {
    get(area, k) { try { return window[area].getItem(k); } catch (e) { return null; } },
    set(area, k, v) { try { window[area].setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

  // ---- consent for third-party marketing code ------------------------------
  function injectTemplate(id, target) {
    const tpl = document.getElementById(id);
    if (!tpl) return;
    const frag = tpl.content.cloneNode(true);
    // Scripts copied from a <template> don't run, so rebuild them.
    frag.querySelectorAll("script").forEach((old) => {
      const s = document.createElement("script");
      Array.from(old.attributes).forEach((a) => s.setAttribute(a.name, a.value));
      s.text = old.text;
      old.replaceWith(s);
    });
    target.appendChild(frag);
  }
  function grantMarketing() {
    injectTemplate("consented-head", document.head);
    injectTemplate("consented-body", document.body);
    window.dataLayer.push({ event: "cmd_consent", consent: "granted" });
  }
  const consent = store.get("localStorage", "cmd-consent");
  const banner = document.getElementById("consent");
  // Strict mode (ANALYTICS_CONSENT_REQUIRED=1): our own visit counting also waits for "That's fine",
  // and nothing analytics-related is written to browser storage until then.
  const strict = document.body.dataset.consentRequired === "1";
  let allowed = !strict || consent === "yes";
  const pending = [];
  const keep = (area, k, v) => { if (allowed) store.set(area, k, v); else pending.push([area, k, v]); };
  const onGrant = [];
  if (consent === "yes") grantMarketing();
  else if (!consent && banner) banner.hidden = false;
  if (banner) banner.addEventListener("click", (e) => {
    const choice = e.target.closest("[data-consent]");
    if (!choice) return;
    store.set("localStorage", "cmd-consent", choice.dataset.consent);
    banner.hidden = true;
    if (choice.dataset.consent === "yes") {
      grantMarketing();
      if (!allowed) { allowed = true; pending.splice(0).forEach((p) => store.set(...p)); onGrant.forEach((fn) => fn()); }
    }
  });

  // ---- identity + attribution -------------------------------------------
  const optedOut = store.get("localStorage", "cmd-optout") === "1";
  let vid = store.get("localStorage", "cmd-vid");
  if (!vid) { vid = uid(); keep("localStorage", "cmd-vid", vid); }

  let sid = store.get("sessionStorage", "cmd-sid");
  const lastSeen = +store.get("localStorage", "cmd-last") || 0;
  if (!sid || Date.now() - lastSeen > SESSION_IDLE_MS) { sid = uid(); keep("sessionStorage", "cmd-sid", sid); keep("sessionStorage", "cmd-attrib", ""); }
  keep("localStorage", "cmd-last", String(Date.now()));

  function readAttribution() {
    const q = new URLSearchParams(location.search);
    const ref = document.referrer && new URL(document.referrer).hostname !== location.hostname ? document.referrer : "";
    if (q.get("utm_source") || q.get("utm_medium") || q.get("utm_campaign")) {
      return { source: (q.get("utm_source") || "unknown").toLowerCase(), medium: (q.get("utm_medium") || "unknown").toLowerCase(), campaign: q.get("utm_campaign") || "", content: q.get("utm_content") || "", referrer: ref };
    }
    if (q.get("gclid")) return { source: "google", medium: "cpc", campaign: "", referrer: ref };
    if (ref) {
      const host = new URL(ref).hostname.replace(/^www\./, "");
      const search = /(^|\.)(google|bing|duckduckgo|yahoo|ecosia)\./.test(host);
      return { source: host, medium: search ? "organic" : "referral", campaign: "", referrer: ref };
    }
    return null;
  }
  let attrib = JSON.parse(store.get("sessionStorage", "cmd-attrib") || "null");
  if (!attrib) {
    // Last non-direct touch wins, remembered for 30 days, so an email click
    // yesterday still gets the credit for a form filled in today.
    const fresh = readAttribution();
    if (fresh) {
      attrib = fresh;
      keep("localStorage", "cmd-touch", JSON.stringify({ ...fresh, at: Date.now() }));
    } else {
      const prior = JSON.parse(store.get("localStorage", "cmd-touch") || "null");
      attrib = prior && Date.now() - prior.at < 30 * 864e5 ? { ...prior, returning: true } : { source: "direct", medium: "none", campaign: "" };
    }
    keep("sessionStorage", "cmd-attrib", JSON.stringify(attrib));
  }

  // ---- queue + transport ----------------------------------------------
  let queue = [];
  // The moments worth seeing in Google Analytics. gtag only exists after cookie consent, so nothing reaches
  // Google before then. (Tag Manager users get every event through dataLayer instead.)
  const GA_EVENTS = new Set(["calc_result", "cta_click", "form_start", "form_error", "quote_view"]);
  function toGoogle(type, data, extra) {
    if (typeof window.gtag !== "function" || !GA_EVENTS.has(type)) return;
    const params = {};
    Object.entries({ ...(data || {}), ...(extra || {}) }).forEach(([k, v]) => {
      if (["string", "number", "boolean"].includes(typeof v)) params[k] = typeof v === "string" ? v.slice(0, 100) : v;
    });
    window.gtag("event", "cmd_" + type, params);
  }
  function cmdTrack(type, data, extra) {
    window.dataLayer.push({ event: "cmd_" + type, cmd_page: page, ...(data || {}), ...(extra || {}) });
    toGoogle(type, data, extra);
    if (optedOut) return;
    queue.push({ type, page, data: data || null, ...(extra || {}) });
    if (queue.length > 200) queue.shift();  // strict mode holds events until consent; don't grow forever
    if (queue.length >= 20) flush();
  }
  function flush(useBeacon) {
    if (!queue.length || optedOut || !allowed) return;
    const body = JSON.stringify({ sid, vid, page, attrib, events: queue.splice(0, 50) });
    if (useBeacon && navigator.sendBeacon) navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "text/plain" }));
    else fetch(ENDPOINT, { method: "POST", body, keepalive: true, headers: { "Content-Type": "text/plain" } }).catch(() => {});
  }
  window.cmdTrack = cmdTrack;
  setInterval(() => flush(false), 5000);
  onGrant.push(() => flush(false));

  // ---- page view -----------------------------------------------------------
  cmdTrack("page_view", { title: document.title, path: location.pathname + location.search });
  if (page === "/quote") cmdTrack("quote_view", null);

  // ---- time on page + per section (only while the tab is visible) ----------
  let visibleSince = document.visibilityState === "visible" ? performance.now() : null;
  let pageMs = 0;
  const sections = new Map(); // el -> { name, on, since, ms, seen }
  const sectionEls = Array.from(document.querySelectorAll("[data-section]"));

  function tick(now) {
    if (visibleSince != null) { pageMs += now - visibleSince; visibleSince = now; }
    sections.forEach((s) => { if (s.on && s.since != null) { s.ms += now - s.since; s.since = now; } });
  }
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      const now = performance.now();
      tick(now);
      entries.forEach((en) => {
        const s = sections.get(en.target);
        const vh = window.innerHeight || 1;
        // "Reading" = at least 35% of the section, or it fills half the screen.
        const on = en.isIntersecting && (en.intersectionRatio >= 0.35 || en.intersectionRect.height >= vh * 0.5);
        s.on = on;
        s.since = on && visibleSince != null ? now : null;
        if (on && !s.seen) { s.seen = true; cmdTrack("section_view", null, { section: s.name }); }
      });
    }, { threshold: [0, 0.2, 0.35, 0.5, 0.75, 1] });
    sectionEls.forEach((el) => { sections.set(el, { name: el.dataset.section, on: false, since: null, ms: 0, seen: false }); io.observe(el); });
  }

  function flushSectionTime() {
    tick(performance.now());
    sections.forEach((s) => {
      if (s.ms >= 500) cmdTrack("section_time", null, { section: s.name, value: Math.round(s.ms) });
      s.ms = 0;
    });
  }

  let maxDepth = 0;
  window.addEventListener("scroll", () => {
    const h = document.documentElement.scrollHeight - innerHeight;
    if (h <= 0) return;
    const depth = Math.floor((scrollY / h) * 4) * 25;
    if (depth > maxDepth) { maxDepth = depth; cmdTrack("scroll_depth", null, { value: depth }); }
  }, { passive: true });

  document.addEventListener("visibilitychange", () => {
    const now = performance.now();
    if (document.visibilityState === "hidden") {
      tick(now);
      visibleSince = null;
      sections.forEach((s) => { s.since = null; });
      flushSectionTime();
      cmdTrack("page_leave", null, { value: Math.round(pageMs) });
      pageMs = 0;
      flush(true);
    } else {
      visibleSince = now;
      sections.forEach((s) => { if (s.on) s.since = now; });
    }
  });
  window.addEventListener("pagehide", () => flush(true));

  // ---- clicks ---------------------------------------------------------------
  document.addEventListener("click", (e) => {
    const cta = e.target.closest("[data-cta]");
    if (cta) { cmdTrack("cta_click", { cta: cta.dataset.cta, href: cta.getAttribute("href") }); flush(true); return; }
    const a = e.target.closest("a[href]");
    if (a) {
      const url = new URL(a.href, location.href);
      const section = a.closest("[data-section]");
      cmdTrack("link_click", { href: url.origin === location.origin ? url.pathname + url.hash : url.href, text: a.textContent.trim().slice(0, 60) }, { section: section ? section.dataset.section : null });
    }
  });

  // ---- forms -----------------------------------------------------------------
  document.querySelectorAll("form[data-attrib-form]").forEach((form) => {
    const set = (name, value) => { const el = form.querySelector(`[name="${name}"]`); if (el) el.value = value; };
    set("sid", sid); set("vid", vid); set("attrib", JSON.stringify(attrib));
    let started = false;
    form.addEventListener("focusin", () => { if (!started) { started = true; cmdTrack("form_start", { form: form.id }); } });
    form.addEventListener("change", (e) => {
      if (e.target.name && !["sid", "vid", "attrib"].includes(e.target.name)) cmdTrack("form_field", { form: form.id, field: e.target.name, filled: !!e.target.value });
    });
    form.addEventListener("submit", () => { cmdTrack("form_field", { form: form.id, field: "_submit_click" }); flush(true); });
    if (form.dataset.errors) cmdTrack("form_error", { form: form.id, fields: form.dataset.errors });
  });

  // Privacy page opt-out button.
  document.querySelectorAll("[data-optout]").forEach((btn) => btn.addEventListener("click", () => {
    store.set("localStorage", "cmd-optout", "1");
    store.set("localStorage", "cmd-consent", "no");
    btn.textContent = "Done. We won't count your visits on this browser.";
    btn.disabled = true;
  }));
})();
