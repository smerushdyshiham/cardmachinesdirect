/* Savings calculator. All fee maths happens on the server (/api/estimate). */
(function () {
  "use strict";

  const MIN_V = 500, MAX_V = 150000, MAX_TYPED = 250000;
  const gbp0 = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 });
  const gbp2 = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const int = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const track = (type, data) => window.cmdTrack && window.cmdTrack(type, data);

  const state = { volume: 10000, atv: 20, current: {} };
  try {
    const saved = JSON.parse(sessionStorage.getItem("cmd-calc") || "null");
    if (saved && saved.volume) Object.assign(state, saved);
  } catch (e) { /* storage unavailable: defaults are fine */ }

  // Slider position (0-1000) <-> takings, on a log scale so £2k and £80k both get room.
  const toVolume = (p) => niceRound(MIN_V * Math.pow(MAX_V / MIN_V, p / 1000));
  const toPos = (v) => Math.round(1000 * Math.log(Math.min(Math.max(v, MIN_V), MAX_V) / MIN_V) / Math.log(MAX_V / MIN_V));
  function niceRound(v) {
    const step = v < 2000 ? 50 : v < 10000 ? 100 : v < 50000 ? 500 : 1000;
    return Math.round(v / step) * step;
  }
  // Accepts "10000", "£10,000", "10k", "£10.5k", "1.2m".
  const parseMoney = (s) => {
    const m = String(s).replace(/[£,\s]/g, "").toLowerCase().match(/^(\d*\.?\d+)([km]?)$/);
    return m ? parseFloat(m[1]) * ({ k: 1e3, m: 1e6 }[m[2]] || 1) : NaN;
  };

  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const outs = (name) => $$(`[data-out="${name}"]`);
  const setText = (name, text) => outs(name).forEach((el) => { el.textContent = text; });
  const setHTML = (name, html) => outs(name).forEach((el) => { el.innerHTML = html; });

  function priceHTML(value) {
    const pounds = Math.floor(value + 1e-9);
    const pence = Math.round((value - pounds) * 100);
    const p = pence === 100 ? [pounds + 1, 0] : [pounds, pence];
    return `<span class="price"><span class="cur">£</span><span class="pounds">${int.format(p[0])}</span><span class="pence">.${String(p[1]).padStart(2, "0")}</span></span>`;
  }

  function paintSlider(el) {
    const pct = ((el.value - el.min) / (el.max - el.min)) * 100;
    el.style.setProperty("--fill", pct + "%");
  }

  // ---- sync inputs from state -------------------------------------------
  function syncInputs(except) {
    $$('[data-input="volume-slider"]').forEach((el) => { if (el !== except) el.value = toPos(state.volume); paintSlider(el); });
    $$('[data-input="volume-text"]').forEach((el) => { if (el !== except) el.value = int.format(state.volume); });
    $$('[data-input="atv-slider"]').forEach((el) => { if (el !== except) el.value = Math.min(200, Math.max(2, Math.round(state.atv))); paintSlider(el); });
    $$('[data-input="atv-text"]').forEach((el) => { if (el !== except) el.value = state.atv; });
    $$("[data-current]").forEach((el) => { if (el !== except && state.current[el.dataset.current] != null) el.value = state.current[el.dataset.current]; });
    setText("volume", gbp0.format(state.volume));
    setText("atv", gbp2.format(state.atv).replace(".00", ""));
    setText("transactions", `That's about ${int.format(Math.round(state.volume / state.atv))} card payments a month.`);
  }

  // ---- request + loading ------------------------------------------------
  let timer = null, seq = 0, firstLoad = true;
  const results = $$("[data-results]");
  const mirrors = $$("[data-calc-mirror]");

  function schedule(reason) {
    clearTimeout(timer);
    seq++;  // new input makes any request already in flight stale, so its answer must never be shown
    results.forEach((el) => { el.classList.add("is-loading"); el.setAttribute("aria-busy", "true"); });
    timer = setTimeout(() => run(reason), 280);
  }

  async function run(reason) {
    const mine = seq;
    const started = performance.now();
    const current = Object.fromEntries(Object.entries(state.current).filter(([, v]) => v !== "" && v != null));
    let data;
    try {
      const res = await fetch("/api/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume: state.volume, atv: state.atv, current }),
      });
      data = await res.json();
      if (!res.ok) throw new Error(data.error || "Something went wrong.");
    } catch (err) {
      if (mine !== seq) return;
      showError(err.message && err.message.length < 120 ? err.message : "We couldn't reach the calculator. Check your connection and try again.");
      return;
    }
    // Hold the loading state briefly so the change registers as a recalculation.
    const wait = Math.max(0, (reduceMotion ? 0 : 450) - (performance.now() - started));
    setTimeout(() => { if (mine === seq) render(data, reason); }, wait);
  }

  function showError(msg) {
    results.forEach((el) => {
      el.classList.remove("is-loading"); el.setAttribute("aria-busy", "false");
      let box = el.querySelector(".error-box");
      if (!box) { box = document.createElement("p"); box.className = "error-box"; el.prepend(box); }
      box.textContent = msg;
    });
  }

  // ---- render -------------------------------------------------------------
  function render(d, reason) {
    $$(".error-box").forEach((b) => b.remove());
    const cheaper = d.we_are_cheaper;
    const byCurrent = d.basis === "current";
    const current = d.rows.find((r) => r.kind === "current");

    setHTML("ours-price", priceHTML(d.ours_monthly));
    setText("compare-label", byCurrent ? "What you pay now" : d.compare_label);
    setText("compare-monthly", gbp2.format(d.compare_monthly));

    if (cheaper) {
      setText("headline-lead", byCurrent ? "You could save about" : "You could save up to");
      setText("tag-lead", byCurrent ? "You could save about" : "You could save up to");
      setHTML("saving-price", priceHTML(d.saving_monthly));
      setText("saving-year", `· ${gbp0.format(d.saving_monthly * 12)} a year`);
      let sub;
      if (byCurrent) {
        sub = `Compared with the ${gbp2.format(current.monthly)} a month your current fees add up to.`;
      } else {
        sub = `Compared with ${d.compare_label}.`;
      }
      setText("headline-sub", sub);
      setText("cta-text", "Want the exact number? Send us a statement and we'll check every line.");
    } else {
      setText("headline-lead", "We can't beat that right now.");
      setHTML("saving-price", priceHTML(0));
      setText("saving-year", "");
      setText("headline-sub", byCurrent
        ? `Your fees come to about ${gbp2.format(current.monthly)} a month. Our cheapest option would be ${gbp2.format(d.ours_monthly)}, so we can't pass on a saving at the moment. A full statement sometimes shows charges you've missed. Send it if you'd like us to double-check.`
        : `At these numbers we can't pass on a saving at the moment.`);
      setText("cta-text", "Prices change. Send us a statement and we'll tell you honestly if that changes.");
    }
    $$("[data-headline]").forEach((el) => el.classList.toggle("not-cheaper", !cheaper));
    $$('[data-show-when="cheaper"]').forEach((el) => { el.hidden = !cheaper; });
    $$('[data-show-when="not-cheaper"]').forEach((el) => { el.hidden = cheaper; });
    // Fees were typed but couldn't be used (no % rate, or a rate that can't be right): say so rather than ignore them.
    const typed = Object.keys(state.current).length > 0;
    const rate = Math.max(state.current.debit_pct || 0, state.current.credit_pct || 0);
    const note = !typed || byCurrent ? "" : rate > 15
      ? "That rate looks too high. Enter it as a percentage, for example 1.2 for 1.2%."
      : "Add your debit or credit card rate too, and we'll compare against what you pay now.";
    outs("current-note").forEach((el) => { el.textContent = note; el.hidden = !note; });

    setText("shelf-sub", `Monthly fees at ${gbp0.format(d.volume)} on card and a ${gbp2.format(d.atv).replace(".00", "")} average sale, cheapest first. Only providers that publish a standard rate are shown.`);
    renderRows(d.rows);

    $$("[data-quote-link]").forEach((a) => {
      const u = new URL(a.href, location.origin);
      u.searchParams.set("volume", Math.round(d.volume));
      a.href = u.pathname + u.search;
    });

    [...results, ...mirrors].forEach((el) => {
      el.classList.remove("is-loading"); el.setAttribute("aria-busy", "false");
      if (!reduceMotion && !firstLoad) { el.classList.remove("is-updating"); void el.offsetWidth; el.classList.add("is-updating"); }
    });
    firstLoad = false;

    try {
      sessionStorage.setItem("cmd-calc", JSON.stringify(state));
      sessionStorage.setItem("cmd-last-saving", JSON.stringify(cheaper ? d.saving_monthly : 0));
    } catch (e) { /* ignore */ }
    if (reason !== "init") {
      track("calc_result", { volume: d.volume, atv: d.atv, basis: d.basis, saving: d.saving_monthly, cheaper });
    }
  }

  function renderRows(rows) {
    const top = Math.max(...rows.map((r) => r.monthly)) || 1;
    outs("rows").forEach((ol) => {
      const prev = {};
      $$("li", ol).forEach((li) => { prev[li.dataset.key] = li.querySelector(".bar i").style.width; });
      ol.innerHTML = rows.map((r, i) => {
        const key = r.kind + ":" + r.name;
        // tabindex: on phones there's no hover, so a tap (focus) opens the note.
        const tip = r.note ? ` data-tip="1" tabindex="0" aria-describedby="tip-${i}"` : "";
        return `<li class="${r.kind}" data-key="${esc(key)}"${tip}>
          <div class="who"><strong>${esc(r.name)}</strong><span>${esc(r.plan)}</span></div>
          <div class="bar" aria-hidden="true"><i style="width:${prev[key] || "0%"}" data-w="${(r.monthly / top * 100).toFixed(1)}%"></i></div>
          <div class="cost"><strong>${gbp2.format(r.monthly)}</strong><span>${r.per_100 != null ? gbp2.format(r.per_100) + " per £100" : "est., before your statement"}</span></div>
          ${r.note ? `<span class="tip" role="tooltip" id="tip-${i}">${esc(r.note)}</span>` : ""}
        </li>`;
      }).join("");
      requestAnimationFrame(() => requestAnimationFrame(() => {
        $$(".bar i", ol).forEach((i) => { i.style.width = i.dataset.w; });
      }));
    });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ---- wire up --------------------------------------------------------------
  let changeTimer = null;
  function changed(source, el) {
    syncInputs(el);
    schedule(source);
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => track("calc_change", { field: source, volume: state.volume, atv: state.atv, has_current: Object.keys(state.current).length > 0 }), 800);
  }

  document.addEventListener("input", (e) => {
    const el = e.target;
    const kind = el.dataset && el.dataset.input;
    if (kind === "volume-slider") { state.volume = toVolume(+el.value); changed("volume", el); }
    else if (kind === "volume-text") {
      const v = parseMoney(el.value);
      if (v > 0) { state.volume = Math.min(MAX_TYPED, Math.max(100, v)); changed("volume", el); }
    }
    else if (kind === "atv-slider") { state.atv = +el.value; changed("atv", el); }
    else if (kind === "atv-text") {
      const v = parseMoney(el.value);
      if (v > 0) { state.atv = Math.min(2000, Math.max(1, v)); changed("atv", el); }
    }
    else if (el.dataset && el.dataset.current) {
      const raw = el.value.replace(/[£%p\s]/g, "");
      if (raw === "") delete state.current[el.dataset.current];
      else if (!isNaN(parseFloat(raw))) state.current[el.dataset.current] = parseFloat(raw);
      changed("current_fees", el);
    }
  });
  // Tidy the typed amount when the visitor leaves the box.
  document.addEventListener("change", (e) => {
    if (e.target.dataset && e.target.dataset.input === "volume-text") e.target.value = int.format(state.volume);
  });

  $$("details[data-track]").forEach((d) => d.addEventListener("toggle", () => { if (d.open) track(d.dataset.track); }));

  syncInputs();
  if (Object.keys(state.current).length) $$('details[data-track="current_fees_open"]').forEach((d) => { d.open = true; });
  run("init");
})();
