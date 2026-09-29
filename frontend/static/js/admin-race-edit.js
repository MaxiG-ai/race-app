// @ts-check
// Race edit page (docs/features/races-and-results.md, classification.md, fueling.md,
// gear.md, access-and-visibility.md, weather.md). Every field autosaves on blur; writes
// return the full owner race detail and only the affected sections are re-rendered so
// focus isn't lost while typing elsewhere.

import { api } from "./api.js";
import { header, fail } from "./layout.js";
import {
  duration, distance, elevation, number, date, esc, parseDuration, DASH, SEGMENTS, SEGMENT_LABELS,
} from "./format.js";

/** @typedef {import("./types.js").RaceDetail} RaceDetail */
/** @typedef {import("./types.js").Recording} Recording */

const SPLIT_LABELS = { swim: "swim", t1: "T1", bike: "bike", t2: "T2", run: "run" };
const FUEL_SEGMENTS_DEFAULT = ["bike", "run"];
const FUEL_SEGMENTS_ALL = /** @type {const} */ (["swim", "t1", "bike", "t2", "run"]);

let raceId = "";
/** @type {any} */
let race = null;
let siteMode = "private";
/** @type {any[]} */
let categories = [];
/** @type {any[]} */
let gearInventory = [];
/** @type {any|null} */
let shoeSuggestion = null;
/** @type {Set<string>} */
let visibleFuelSegments = new Set();
/** @type {Record<string, {carbs_g: number|null, products: {name: string, quantity: number|null}[], notes: string|null}>} */
let fuelingDraft = {};

async function main() {
  const main = /** @type {HTMLElement} */ (document.getElementById("main"));
  let who;
  try {
    who = await header();
  } catch (err) {
    fail(main, err);
    return;
  }
  if (!who.owner) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    return;
  }
  siteMode = who.site_mode;

  raceId = new URLSearchParams(location.search).get("id") ?? "";
  if (!raceId) {
    fail(main, new Error("No race id given."));
    return;
  }

  main.innerHTML = `
    <div class="page-head">
      <h1 id="page-title">Race</h1>
      <a class="small" id="view-link" href="#">View public page</a>
    </div>
    <div class="card" id="completeness-section"></div>

    <section class="section" id="meta-section"></section>
    <section class="section" id="result-section"></section>
    <section class="section" id="visibility-section"></section>
    <section class="section" id="category-section"></section>
    <section class="section" id="weather-section"></section>
    <section class="section" id="fueling-section"></section>
    <section class="section" id="gear-section"></section>
    <section class="section" id="photos-section"></section>
    <section class="section" id="recordings-section"></section>
    <section class="section danger-zone" id="danger-section"></section>
  `;

  try {
    const [raceData, cats, gear, shoes] = await Promise.all([
      api(`/races/${raceId}`),
      api("/categories"),
      api("/gear"),
      api("/gear/last-used-shoes"),
    ]);
    race = raceData;
    categories = cats;
    gearInventory = gear;
    shoeSuggestion = shoes;
  } catch (err) {
    fail(main, err);
    return;
  }

  visibleFuelSegments = new Set([...FUEL_SEGMENTS_DEFAULT, ...race.fueling.map((/** @type {any} */ f) => f.segment)]);
  syncFuelingDraft();

  document.getElementById("page-title").textContent = race.name || "Untitled race";
  const viewLink = /** @type {HTMLAnchorElement} */ (document.getElementById("view-link"));
  viewLink.href = `/races/${raceId}`;

  wireMeta();
  wireResult();
  wireVisibility();
  wireCategory();
  wireWeather();
  wireFueling();
  wireGear();
  wirePhotos();
  wireRecordings();
  wireDanger();

  renderCompleteness();
  renderMeta();
  renderResult();
  renderVisibility();
  renderCategory();
  renderWeather();
  renderFueling();
  renderGear();
  renderPhotos();
  renderRecordings();
}

// --- shared save helper ------------------------------------------------------------------

/**
 * @param {string} path @param {string} method @param {any} body
 * @param {HTMLElement|null} statusEl @param {() => void} [after]
 */
async function saveField(path, method, body, statusEl, after) {
  if (statusEl) { statusEl.textContent = "Saving…"; statusEl.className = "field-status"; }
  try {
    const result = await api(path, { method, body });
    if (result) race = result;
    if (statusEl) { statusEl.textContent = "Saved"; statusEl.className = "field-status ok"; }
    after?.();
    renderCompleteness();
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
  }
}

function renderCompleteness() {
  const el = /** @type {HTMLElement} */ (document.getElementById("completeness-section"));
  const c = race.completeness ?? {};
  const labels = {
    official_result: "Official result", fueling: "Fueling", classification: "Classification",
    comparison: "Comparison", public: "What visitors would see",
  };
  el.innerHTML = `<h2 class="small muted" style="margin-top:0">Completeness</h2>
    <ul class="checklist">
      ${Object.entries(c).map(([key, info]) => {
        const i = /** @type {{missing?: string[], usable: boolean}} */ (info);
        const done = (!i.missing || i.missing.length === 0) && i.usable !== false;
        const detail = i.missing?.length ? `missing ${i.missing.join(", ")}` : "";
        return `<li class="${done ? "done" : "todo"}"><span class="mark">${done ? "✓" : "·"}</span>
          <span>${esc(labels[/** @type {keyof typeof labels} */ (key)] ?? key)}
          ${detail ? `<span class="detail"> — ${esc(detail)}</span>` : ""}</span></li>`;
      }).join("")}
    </ul>`;
}

// --- metadata ------------------------------------------------------------------------------

function metaRow(id, field, label, inputHtml) {
  return `<div class="field-row">
    <label for="${id}">${label}</label>${inputHtml}
    <span class="field-status" id="${id}-status"></span>
  </div>`;
}

function renderMeta() {
  const el = /** @type {HTMLElement} */ (document.getElementById("meta-section"));
  el.innerHTML = `<h2>Race</h2>
    ${metaRow("m-name", "name", "Name",
      `<input id="m-name" data-field="name" value="${esc(race.name)}">`)}
    ${metaRow("m-date", "date", "Date",
      `<input id="m-date" type="date" data-field="date" value="${esc(race.date)}">`)}
    ${metaRow("m-loc", "location_name", "Location",
      `<input id="m-loc" data-field="location_name" value="${esc(race.location_name ?? "")}">`)}
    ${metaRow("m-lat", "location_lat", "Latitude",
      `<input id="m-lat" type="number" step="any" min="-90" max="90" data-field="location_lat"
        value="${race.location_lat ?? ""}">`)}
    ${metaRow("m-lng", "location_lng", "Longitude",
      `<input id="m-lng" type="number" step="any" min="-180" max="180" data-field="location_lng"
        value="${race.location_lng ?? ""}">`)}
    <div class="field">
      <label for="m-notes">Race report</label>
      <textarea id="m-notes" data-field="notes" aria-describedby="m-notes-help">${esc(race.notes ?? "")}</textarea>
      <span class="field-status" id="m-notes-status"></span>
      <p class="small muted" id="m-notes-help">Markdown: <code># heading</code>,
        <code>- list</code>, <code>&gt; quote</code>, <code>**bold**</code>, <code>*italic*</code>,
        <code>[link](https://…)</code>. Anything else is shown as typed.</p>
      ${race.notes_html ? `<details><summary>Preview</summary>
        <div class="prose card">${race.notes_html}</div></details>` : ""}
    </div>`;
}

function wireMeta() {
  const el = /** @type {HTMLElement} */ (document.getElementById("meta-section"));
  el.addEventListener("focusout", (e) => {
    const target = /** @type {HTMLInputElement|HTMLTextAreaElement} */ (e.target);
    const field = target.getAttribute?.("data-field");
    if (!field) return;
    const statusEl = document.getElementById(`${target.id}-status`);
    const raw = target.value.trim();
    /** @type {any} */
    let body;
    if (field === "name") {
      if (!raw) { setStatus(statusEl, "Name can't be blank.", true); return; }
      body = { name: raw };
    } else if (field === "date") {
      if (!raw) { setStatus(statusEl, "Date can't be blank.", true); return; }
      body = { date: raw };
    } else if (field === "location_lat" || field === "location_lng") {
      if (raw === "") { body = { [field]: null }; }
      else {
        const n = Number(raw);
        if (!Number.isFinite(n)) { setStatus(statusEl, "Enter a number.", true); return; }
        body = { [field]: n };
      }
    } else {
      body = { [field]: raw === "" ? null : raw };
    }
    saveField(`/races/${raceId}`, "PATCH", body, statusEl, () => { renderMeta(); renderWeather(); });
  });
}

/** @param {Element|null} el @param {string} text @param {boolean} isErr */
function setStatus(el, text, isErr) {
  if (!el) return;
  el.textContent = text;
  el.className = `field-status ${isErr ? "err" : ""}`;
}

// --- official result -------------------------------------------------------------------

function splitFieldHtml(seg) {
  const r = race.official_result;
  const value = r ? r[`${seg}_time`] : null;
  const derived = r && r.derived_split === seg;
  return `<div class="split-field">
    <label for="or-${seg}">${SPLIT_LABELS[seg]}</label>
    <input id="or-${seg}" data-or-field="${seg}_time" placeholder="h:mm:ss"
      value="${value != null ? duration(value) : ""}" ${derived ? "disabled" : ""}>
    ${derived ? `<div class="split-note">calculated</div>` : ""}
    <div class="field-status" id="or-${seg}-status"></div>
  </div>`;
}

function renderResult() {
  const el = /** @type {HTMLElement} */ (document.getElementById("result-section"));
  const r = race.official_result;
  el.innerHTML = `<h2>Official result</h2>
    <div class="split-grid">${SEGMENTS.map(splitFieldHtml).join("")}</div>
    <div class="field-row">
      <label for="or-total">Total</label>
      <input id="or-total" data-or-field="total_time" placeholder="h:mm:ss"
        value="${r?.total_time != null ? duration(r.total_time) : ""}">
      <span class="field-status" id="or-total-status"></span>
      ${r?.total_is_derived ? `<span class="small muted">calculated from splits</span>` : ""}
    </div>
    <p class="result-summary" id="or-summary"></p>
    <div class="fields">
      <div class="field"><label for="or-overall_place">Overall place</label>
        <input id="or-overall_place" type="text" inputmode="numeric" data-or-field="overall_place"
          value="${r?.overall_place ?? ""}"><span class="field-status" id="or-overall_place-status"></span></div>
      <div class="field"><label for="or-age_group_place">Age-group place</label>
        <input id="or-age_group_place" type="text" inputmode="numeric" data-or-field="age_group_place"
          value="${r?.age_group_place ?? ""}"><span class="field-status" id="or-age_group_place-status"></span></div>
    </div>`;
  updateResultLive();
}

function updateResultLive() {
  const summary = document.getElementById("or-summary");
  if (!summary) return;
  const totalInput = /** @type {HTMLInputElement} */ (document.getElementById("or-total"));
  const totalStr = totalInput.value.trim();
  if (totalStr) { summary.textContent = ""; summary.className = "result-summary"; return; }
  const missing = [];
  let sum = 0;
  let allValid = true;
  for (const seg of SEGMENTS) {
    const input = /** @type {HTMLInputElement} */ (document.getElementById(`or-${seg}`));
    const v = input.value.trim();
    if (!v) { missing.push(SPLIT_LABELS[seg]); allValid = false; continue; }
    const parsed = parseDuration(v);
    if (Number.isNaN(parsed)) { allValid = false; continue; }
    sum += parsed;
  }
  if (missing.length) {
    summary.textContent = `Enter the total time, or fill in every split so it can be calculated. `
      + `Still missing: ${missing.join(", ")}.`;
    summary.className = "result-summary err";
  } else if (allValid) {
    summary.textContent = `Total: ${duration(sum)} (calculated)`;
    summary.className = "result-summary";
  } else {
    summary.textContent = "";
    summary.className = "result-summary";
  }
}

function wireResult() {
  const el = /** @type {HTMLElement} */ (document.getElementById("result-section"));
  el.addEventListener("input", (e) => {
    if (/** @type {HTMLElement} */ (e.target).getAttribute?.("data-or-field")) updateResultLive();
  });
  el.addEventListener("focusout", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    const field = target.getAttribute?.("data-or-field");
    if (!field || target.disabled) return;
    const statusEl = document.getElementById(`${target.id}-status`);
    // The server derives the total from the splits atomically (races-and-results.md#official-total-time):
    // a save that omits an already-entered split would look like a partial write, but the row
    // isn't valid until the whole total/splits invariant holds. So every blur in this section
    // sends the section's whole current (valid) state, not just the field that was touched —
    // every other section still autosaves one field at a time.
    const body = collectResultForm(statusEl);
    if (!body) return;
    saveField(`/races/${raceId}/official-result`, "PUT", body, statusEl, () => renderResult());
  });
}

/** Reads every official-result field currently in the DOM. Returns null (after showing an
 * inline error on the offending field) if something typed can't be parsed. */
function collectResultForm(fallbackStatusEl) {
  /** @type {Record<string, number|null>} */
  const body = {};
  for (const field of [...SEGMENTS.map((s) => `${s}_time`), "total_time"]) {
    const input = /** @type {HTMLInputElement} */ (document.getElementById(`or-${field.replace("_time", "")}`));
    if (input.disabled) continue; // the calculated split: let the server keep deriving it
    const raw = input.value.trim();
    if (raw === "") { body[field] = null; continue; }
    const secs = parseDuration(raw);
    if (Number.isNaN(secs)) {
      setStatus(document.getElementById(`${input.id}-status`) ?? fallbackStatusEl, "Use h:mm:ss.", true);
      return null;
    }
    body[field] = secs;
  }
  for (const field of ["overall_place", "age_group_place"]) {
    const input = /** @type {HTMLInputElement} */ (document.getElementById(`or-${field}`));
    const raw = input.value.trim();
    if (raw === "") { body[field] = null; continue; }
    const n = parseInt(raw, 10);
    if (!Number.isInteger(n) || n < 1) {
      setStatus(document.getElementById(`${input.id}-status`) ?? fallbackStatusEl, "Enter a whole number.", true);
      return null;
    }
    body[field] = n;
  }
  return body;
}

// --- visibility ----------------------------------------------------------------------------

function renderVisibility() {
  const el = /** @type {HTMLElement} */ (document.getElementById("visibility-section"));
  const line = siteMode === "public"
    ? "Visitors see the result, recorded splits, map, weather, fueling, gear names and notes. "
      + "They never see uploads, device serials or this checklist."
    : "The site is private, so this has no effect yet.";
  el.innerHTML = `<h2>Visibility</h2>
    <label class="field-row"><input type="checkbox" id="v-public" ${race.is_public ? "checked" : ""}> Public</label>
    <p class="small muted">${esc(line)}</p>
    <span class="field-status" id="v-status"></span>`;
}

function wireVisibility() {
  const el = /** @type {HTMLElement} */ (document.getElementById("visibility-section"));
  el.addEventListener("change", async (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    if (target.id !== "v-public") return;
    const statusEl = document.getElementById("v-status");
    try {
      await api(`/races/${raceId}/visibility`, { method: "PATCH", body: { is_public: target.checked } });
      race.is_public = target.checked;
      setStatus(statusEl, "Saved", false);
    } catch (err) {
      target.checked = !target.checked;
      setStatus(statusEl, err instanceof Error ? err.message : String(err), true);
    }
  });
}

// --- category --------------------------------------------------------------------------

function renderCategory() {
  const el = /** @type {HTMLElement} */ (document.getElementById("category-section"));
  const c = race.classification ?? { source: "unclassified", auto_category: null, detail: null };
  let line;
  if (c.source === "auto") {
    line = "Calculated from the measured distances.";
  } else if (c.source === "manual") {
    line = "Set by you.";
    if (c.auto_category && (!race.category || c.auto_category.id !== race.category.id)) {
      line += ` The measured distances suggest ${esc(c.auto_category.label)}.`;
    }
  } else if (c.detail) {
    const pct = Math.abs(c.detail.delta_pct).toFixed(1);
    const dir = c.detail.delta_pct < 0 ? "short" : "long";
    line = `No band matched. Closest is ${esc(c.detail.nearest_label)}, but the `
      + `${esc(c.detail.segment)} is ${pct} % ${dir} of it.`;
  } else {
    line = "No band matched, and there isn't enough measured distance to say how close.";
  }
  const options = categories.filter((cat) => cat.is_active).map((cat) =>
    `<option value="${cat.id}" ${race.category?.id === cat.id ? "selected" : ""}>${esc(cat.label)}</option>`).join("");
  el.innerHTML = `<h2>Category</h2>
    <p>${race.category ? `<span class="badge">${esc(race.category.label)}</span>` : `<span class="muted">Unclassified</span>`}</p>
    <p class="small muted">${line}</p>
    <div class="field-row">
      <label for="cat-select">Set category</label>
      <select id="cat-select"><option value="">— none —</option>${options}</select>
      <span class="field-status" id="cat-status"></span>
    </div>
    ${c.source === "manual" ? `<button type="button" class="link" id="cat-use-auto">Use the calculated category</button>` : ""}`;
}

function wireCategory() {
  const el = /** @type {HTMLElement} */ (document.getElementById("category-section"));
  el.addEventListener("change", (e) => {
    const target = /** @type {HTMLSelectElement} */ (e.target);
    if (target.id !== "cat-select") return;
    const statusEl = document.getElementById("cat-status");
    const body = { race_category_id: target.value === "" ? null : Number(target.value) };
    saveField(`/races/${raceId}`, "PATCH", body, statusEl, () => renderCategory());
  });
  el.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (target.id !== "cat-use-auto") return;
    saveField(`/races/${raceId}`, "PATCH", { race_category_id: null }, null, () => renderCategory());
  });
}

// --- weather --------------------------------------------------------------------------

function renderWeather() {
  const el = /** @type {HTMLElement} */ (document.getElementById("weather-section"));
  const w = race.weather ?? {};
  const hasLocation = race.location_lat != null && race.location_lng != null;
  el.innerHTML = `<h2>Weather</h2>
    ${hasLocation ? `
      <p class="tnum">${number(w.temp_c, "°C", 1)} · ${number(w.humidity_pct, "%", 0)}
        · ${esc(w.conditions ?? DASH)}</p>
      <p class="small muted">${race.weather_fetched_at
        ? `Fetched ${esc(new Date(race.weather_fetched_at).toLocaleString())}` : "Not fetched yet."}</p>
      <button type="button" id="weather-refresh">Refresh</button>
      <span class="field-status" id="weather-status"></span>`
      : `<p class="muted small">Add a location to fetch weather.</p>`}`;
}

function wireWeather() {
  const el = /** @type {HTMLElement} */ (document.getElementById("weather-section"));
  el.addEventListener("click", (e) => {
    if (/** @type {HTMLElement} */ (e.target).id !== "weather-refresh") return;
    const statusEl = document.getElementById("weather-status");
    saveField(`/races/${raceId}/weather/refresh`, "POST", undefined, statusEl, () => renderWeather());
  });
}

// --- fueling --------------------------------------------------------------------------

function syncFuelingDraft() {
  fuelingDraft = {};
  for (const seg of visibleFuelSegments) {
    const existing = race.fueling.find((/** @type {any} */ f) => f.segment === seg);
    fuelingDraft[seg] = existing
      ? { carbs_g: existing.carbs_g, products: existing.products.map((/** @type {any} */ p) => ({ ...p })), notes: existing.notes }
      : { carbs_g: null, products: [], notes: null };
  }
}

function fuelIsEmpty(seg) {
  const f = fuelingDraft[seg];
  return f.carbs_g == null && !f.notes && f.products.every((p) => !p.name?.trim() && p.quantity == null);
}

function fuelSegmentHtml(seg) {
  const f = fuelingDraft[seg];
  return `<div class="fueling-segment" data-fuel-segment="${seg}">
    <h3>${esc(SEGMENT_LABELS[seg])}</h3>
    <div class="field-row">
      <label for="fuel-${seg}-carbs">Carbs (g)</label>
      <input id="fuel-${seg}-carbs" type="number" min="0" data-fuel-field="carbs_g"
        value="${f.carbs_g ?? ""}">
    </div>
    <div class="products" data-fuel-products>
      ${f.products.map((p, i) => `<div class="product-row" data-prod-index="${i}">
        <input type="text" placeholder="Product" data-prod-name value="${esc(p.name)}">
        <input type="number" min="0" step="any" placeholder="Qty" data-prod-qty value="${p.quantity ?? ""}">
        <button type="button" class="link" data-prod-remove="${i}">Remove</button>
      </div>`).join("")}
    </div>
    <button type="button" data-prod-add>Add product</button>
    <div class="field">
      <label for="fuel-${seg}-notes">Notes</label>
      <textarea id="fuel-${seg}-notes" data-fuel-field="notes">${esc(f.notes ?? "")}</textarea>
    </div>
    <span class="field-status" id="fuel-${seg}-status"></span>
  </div>`;
}

function renderFueling() {
  const el = /** @type {HTMLElement} */ (document.getElementById("fueling-section"));
  const hidden = FUEL_SEGMENTS_ALL.filter((s) => !visibleFuelSegments.has(s));
  el.innerHTML = `<h2>Fueling</h2>
    ${[...visibleFuelSegments].sort((a, b) => FUEL_SEGMENTS_ALL.indexOf(/** @type {any} */(a)) - FUEL_SEGMENTS_ALL.indexOf(/** @type {any} */(b)))
      .map(fuelSegmentHtml).join("")}
    ${hidden.length ? `<p class="small">
      ${hidden.map((s) => `<button type="button" data-add-segment="${s}">Add ${esc(SEGMENT_LABELS[s])}</button>`).join(" ")}
    </p>` : ""}`;
}

function wireFueling() {
  const el = /** @type {HTMLElement} */ (document.getElementById("fueling-section"));
  el.addEventListener("input", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    const wrap = target.closest("[data-fuel-segment]");
    if (!wrap) return;
    const seg = wrap.getAttribute("data-fuel-segment");
    const f = fuelingDraft[seg];
    if (target.hasAttribute("data-fuel-field")) {
      const field = target.getAttribute("data-fuel-field");
      if (field === "carbs_g") f.carbs_g = target.value === "" ? null : Number(target.value);
      else if (field === "notes") f.notes = target.value === "" ? null : target.value;
    } else {
      const row = target.closest("[data-prod-index]");
      if (!row) return;
      const idx = Number(row.getAttribute("data-prod-index"));
      if (target.hasAttribute("data-prod-name")) f.products[idx].name = target.value;
      if (target.hasAttribute("data-prod-qty")) f.products[idx].quantity = target.value === "" ? null : Number(target.value);
    }
  });
  el.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const addSeg = target.getAttribute("data-add-segment");
    if (addSeg) {
      visibleFuelSegments.add(addSeg);
      if (!fuelingDraft[addSeg]) fuelingDraft[addSeg] = { carbs_g: null, products: [], notes: null };
      renderFueling();
      return;
    }
    const wrap = target.closest("[data-fuel-segment]");
    if (!wrap) return;
    const seg = wrap.getAttribute("data-fuel-segment");
    if (target.hasAttribute("data-prod-add")) {
      fuelingDraft[seg].products.push({ name: "", quantity: null });
      renderFueling();
      return;
    }
    const removeIdx = target.getAttribute("data-prod-remove");
    if (removeIdx !== null) {
      fuelingDraft[seg].products.splice(Number(removeIdx), 1);
      renderFueling();
      saveFueling(seg);
    }
  });
  el.addEventListener("focusout", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const wrap = target.closest("[data-fuel-segment]");
    if (!wrap) return;
    const seg = wrap.getAttribute("data-fuel-segment");
    saveFueling(seg);
  });
}

/** @param {string|null} seg */
function saveFueling(seg) {
  const statusEl = seg ? document.getElementById(`fuel-${seg}-status`) : null;
  const body = [...visibleFuelSegments]
    .filter((s) => !fuelIsEmpty(s))
    .map((s) => {
      const f = fuelingDraft[s];
      return {
        segment: s,
        carbs_g: f.carbs_g,
        products: f.products.filter((p) => p.name?.trim()),
        notes: f.notes,
      };
    });
  saveField(`/races/${raceId}/fueling`, "PUT", body, statusEl, () => {
    syncFuelingDraft();
    renderFueling();
  });
}

// --- gear -----------------------------------------------------------------------------

function renderGear() {
  const el = /** @type {HTMLElement} */ (document.getElementById("gear-section"));
  const primaries = (race.recordings ?? []).filter((/** @type {any} */ r) => r.is_primary && !r.segment.startsWith("t"));
  const active = gearInventory.filter((g) => !g.retired_at);
  if (!primaries.length) {
    el.innerHTML = `<h2>Gear</h2><p class="muted small">No recordings yet.</p>`;
    return;
  }
  el.innerHTML = `<h2>Gear</h2>
    ${primaries.map((r) => {
      const currentIds = new Set(r.gear.map((/** @type {any} */ g) => g.id));
      const suggested = r.segment === "run" && currentIds.size === 0 && shoeSuggestion ? shoeSuggestion.id : null;
      return `<div class="gear-picker" data-activity="${r.id}">
        <strong>${esc(SEGMENT_LABELS[r.segment])}</strong>
        ${suggested ? `<div class="suggestion">Suggested: ${esc(shoeSuggestion.name)} (last used) — check to confirm.</div>` : ""}
        <div class="options">
          ${active.map((g) => `<label><input type="checkbox" value="${g.id}"
            ${currentIds.has(g.id) || g.id === suggested ? "checked" : ""}>
            ${esc(g.name)} <span class="muted">(${esc(g.kind)})</span></label>`).join("")}
        </div>
        <span class="field-status" id="gear-status-${r.id}"></span>
      </div>`;
    }).join("")}`;
}

function wireGear() {
  const el = /** @type {HTMLElement} */ (document.getElementById("gear-section"));
  el.addEventListener("change", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    const picker = target.closest("[data-activity]");
    if (!picker) return;
    const activityId = picker.getAttribute("data-activity");
    const ids = [...picker.querySelectorAll("input[type=checkbox]:checked")]
      .map((i) => Number(/** @type {HTMLInputElement} */ (i).value));
    const statusEl = document.getElementById(`gear-status-${activityId}`);
    saveField(`/activities/${activityId}`, "PATCH", { gear_ids: ids }, statusEl, () => renderGear());
  });
}

// --- recordings -------------------------------------------------------------------------

// --- photos ---------------------------------------------------------------------------------

function renderPhotos() {
  const el = /** @type {HTMLElement} */ (document.getElementById("photos-section"));
  /** @type {import("./types.js").Photo[]} */
  const list = race.photos;
  el.innerHTML = `<h2>Photos</h2>
    <p class="small muted">JPEG, PNG or WebP. Location, camera and other metadata are removed
      before anything is stored. The cover heads the timeline card and link previews.</p>
    <p><label class="button" for="photo-input">Add photos</label>
      <input id="photo-input" class="hidden" type="file" multiple
        accept="image/jpeg,image/png,image/webp"></p>
    <ul class="result-list" id="photo-results"></ul>
    <ul class="photo-admin">
      ${list.map((p, i) => `<li data-photo-id="${p.id}">
        <img src="${esc(p.thumb_url)}" alt="" loading="lazy" width="${p.width}" height="${p.height}">
        <div class="photo-admin-fields">
          <label for="photo-${p.id}-caption">Caption</label>
          <input id="photo-${p.id}-caption" data-photo-caption value="${esc(p.caption ?? "")}">
          <span class="field-status" id="photo-${p.id}-status"></span>
          <div class="photo-admin-actions">
            ${p.is_cover ? `<span class="badge">cover</span>`
              : `<button type="button" data-photo-cover>Make cover</button>`}
            <button type="button" data-photo-move="-1" ${i === 0 ? "disabled" : ""}
              aria-label="Move earlier">&uarr;</button>
            <button type="button" data-photo-move="1" ${i === list.length - 1 ? "disabled" : ""}
              aria-label="Move later">&darr;</button>
            <button type="button" class="link" data-photo-delete>Delete</button>
          </div>
        </div>
      </li>`).join("")}
    </ul>`;
}

async function reloadPhotos() {
  race = await api(`/races/${raceId}`);
  renderPhotos();
  renderCompleteness();
}

function wirePhotos() {
  const el = /** @type {HTMLElement} */ (document.getElementById("photos-section"));
  el.addEventListener("change", async (e) => {
    const input = /** @type {HTMLInputElement} */ (e.target);
    if (input.id !== "photo-input" || !input.files?.length) return;
    const form = new FormData();
    for (const file of input.files) form.append("files", file);
    const results = /** @type {HTMLElement} */ (document.getElementById("photo-results"));
    results.innerHTML = `<li class="result-line">Uploading…</li>`;
    try {
      /** @type {{filename: string|null, result: string, message: string}[]} */
      const outcomes = await api(`/races/${raceId}/photos`, { method: "POST", form });
      await reloadPhotos();
      const again = /** @type {HTMLElement} */ (document.getElementById("photo-results"));
      again.innerHTML = outcomes.filter((o) => o.result !== "added").map((o) =>
        `<li class="result-line ${esc(o.result)}">${esc(o.filename ?? "")}: ${esc(o.message)}</li>`).join("");
    } catch (err) {
      results.innerHTML = `<li class="result-line failed">${esc(err instanceof Error ? err.message : err)}</li>`;
    }
  });
  el.addEventListener("focusout", async (e) => {
    const input = /** @type {HTMLInputElement} */ (e.target);
    if (!input.hasAttribute?.("data-photo-caption")) return;
    const id = input.closest("[data-photo-id]")?.getAttribute("data-photo-id");
    const statusEl = document.getElementById(`photo-${id}-status`);
    const current = race.photos.find((/** @type {any} */ p) => String(p.id) === id);
    const value = input.value.trim() || null;
    if (current && current.caption === value) return;
    setStatus(statusEl, "Saving…", false);
    try {
      await api(`/races/${raceId}/photos/${id}`, { method: "PATCH", body: { caption: value } });
      if (current) current.caption = value;
      setStatus(statusEl, "Saved", false);
    } catch (err) {
      setStatus(statusEl, err instanceof Error ? err.message : String(err), true);
    }
  });
  el.addEventListener("click", async (e) => {
    const button = /** @type {HTMLElement} */ (e.target).closest("button");
    const item = button?.closest("[data-photo-id]");
    if (!button || !item) return;
    const id = item.getAttribute("data-photo-id");
    const statusEl = document.getElementById(`photo-${id}-status`);
    try {
      if (button.hasAttribute("data-photo-cover")) {
        await api(`/races/${raceId}/photos/${id}`, { method: "PATCH", body: { is_cover: true } });
      } else if (button.hasAttribute("data-photo-move")) {
        // Positions are rewritten for the whole list, so the order is always 0..n-1.
        const ids = race.photos.map((/** @type {any} */ p) => String(p.id));
        const from = ids.indexOf(/** @type {string} */ (id));
        const to = from + Number(button.getAttribute("data-photo-move"));
        [ids[from], ids[to]] = [ids[to], ids[from]];
        for (const [position, photoId] of ids.entries()) {
          await api(`/races/${raceId}/photos/${photoId}`, { method: "PATCH", body: { position } });
        }
      } else if (button.hasAttribute("data-photo-delete")) {
        if (!confirm("Delete this photo?")) return;
        await api(`/races/${raceId}/photos/${id}`, { method: "DELETE" });
      } else {
        return;
      }
      await reloadPhotos();
    } catch (err) {
      setStatus(statusEl, err instanceof Error ? err.message : String(err), true);
    }
  });
}

function renderRecordings() {
  const el = /** @type {HTMLElement} */ (document.getElementById("recordings-section"));
  /** @type {Map<string, any[]>} */
  const bySegment = new Map();
  for (const r of race.recordings ?? []) {
    if (!bySegment.has(r.segment)) bySegment.set(r.segment, []);
    bySegment.get(r.segment).push(r);
  }
  const multi = [...bySegment.entries()].filter(([, list]) => list.length > 1);
  if (!multi.length) {
    el.innerHTML = `<h2>Recordings</h2><p class="muted small">Every segment has one recording.</p>`;
    return;
  }
  el.innerHTML = `<h2>Recordings</h2>
    ${multi.map(([seg, list]) => `<div>
      <strong>${esc(SEGMENT_LABELS[seg])}</strong>
      ${list.map((r) => `<label class="recording-switch">
        <input type="radio" name="primary-${seg}" value="${r.id}" ${r.is_primary ? "checked" : ""}>
        ${esc(r.device_key ?? `Recording ${r.id}`)}
        <span class="small muted tnum">${distance(r.distance_m)} · ${duration(r.elapsed_time)}</span>
      </label>`).join("")}
      <span class="field-status" id="rec-status-${seg}"></span>
    </div>`).join("")}`;
}

function wireRecordings() {
  const el = /** @type {HTMLElement} */ (document.getElementById("recordings-section"));
  el.addEventListener("change", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    if (target.type !== "radio") return;
    const seg = target.name.replace("primary-", "");
    const statusEl = document.getElementById(`rec-status-${seg}`);
    saveField(`/activities/${target.value}`, "PATCH", { is_primary: true }, statusEl, () => {
      renderRecordings();
      renderGear();
      renderCategory();
    });
  });
}

// --- danger zone ----------------------------------------------------------------------

function wireDanger() {
  const el = /** @type {HTMLElement} */ (document.getElementById("danger-section"));
  el.innerHTML = `<h2>Danger zone</h2>
    <p class="small muted">Deleting sends this race's uploads back to the import tray instead of
      deleting them.</p>
    <button type="button" id="delete-race">Delete race</button>
    <span class="field-status" id="delete-status"></span>`;
  document.getElementById("delete-race")?.addEventListener("click", async () => {
    if (!confirm(`Delete "${race.name}"? Its uploads go back to the tray. This can't be undone.`)) return;
    try {
      await api(`/races/${raceId}`, { method: "DELETE" });
      location.href = "/";
    } catch (err) {
      setStatus(document.getElementById("delete-status"), err instanceof Error ? err.message : String(err), true);
    }
  });
}

main();
