// @ts-check
// Race page for the owner: the race's files at the top, with whether they are complete
// for its race type, then everything else about the race (docs/features/races-and-results.md,
// import.md, grouping.md, classification.md, fueling.md, gear.md, access-and-visibility.md,
// weather.md). Without an id it is the new-race page. Every field autosaves on blur; writes
// return the full owner race detail and only the affected sections are re-rendered so
// focus isn't lost while typing elsewhere.

import { api } from "./api.js";
import { header, fail } from "./layout.js";
import {
  duration, distance, number, esc, parseDuration, DASH, SEGMENT_LABELS,
  RACE_TYPES, RACE_TYPE_LABELS, raceSegments,
} from "./format.js";

/** @typedef {import("./types.js").RaceDetail} RaceDetail */
/** @typedef {import("./types.js").Recording} Recording */
/** @typedef {import("./types.js").RaceUpload} RaceUpload */
/**
 * @typedef {{filename: string|null, result: "attached"|"needs_segment"|"duplicate"|"failed",
 *   message: string, upload_id: number|null, race_id: number|null}} Outcome
 */

const SPLIT_LABELS = { swim: "swim", t1: "T1", bike: "bike", t2: "T2", run: "run" };

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

/** @param {string} id */
const byId = (id) => /** @type {HTMLElement} */ (document.getElementById(id));

async function main() {
  const main = byId("main");
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

  let raceData = null;
  try {
    const [cats, gear, shoes] = await Promise.all([
      api("/categories"),
      api("/gear"),
      api("/gear/last-used-shoes"),
    ]);
    categories = cats;
    gearInventory = gear;
    shoeSuggestion = shoes;
    if (raceId) raceData = await api(`/races/${raceId}`);
  } catch (err) {
    fail(main, err);
    return;
  }
  if (raceData) openRace(main, raceData, []);
  else renderNewRace(main);
}

// --- new race ------------------------------------------------------------------------------

/** No id yet: pick a type (or let the files decide), then drop the files, or start empty. */
function renderNewRace(/** @type {HTMLElement} */ main) {
  main.innerHTML = `
    <div class="page-head"><h1>New race</h1></div>
    <section class="card files-card">
      <div class="field-row">
        <label for="n-type">Race type</label>
        <select id="n-type">
          <option value="">Detect from the files</option>
          ${RACE_TYPES.map((t) => `<option value="${t}">${esc(RACE_TYPE_LABELS[t])}</option>`).join("")}
        </select>
      </div>
      ${dropzoneHtml("Drop the FIT files of one race here, or click to choose.")}
      <ul class="result-list" id="file-results" aria-live="polite"></ul>
      <p class="small muted">No files? <button type="button" class="link" id="n-manual">Create the
        race without files</button> and fill it in by hand.</p>
    </section>`;
  const typeSelect = /** @type {HTMLSelectElement} */ (byId("n-type"));

  wireDropzone(async (files) => {
    showUploading(files);
    const form = new FormData();
    for (const file of files) form.append("files", file);
    if (typeSelect.value) form.append("race_type", typeSelect.value);
    try {
      /** @type {{outcomes: Outcome[], race: any}} */
      const out = await api("/races/import", { method: "POST", form });
      if (!out.race) {
        renderOutcomes(out.outcomes);
        return;
      }
      history.replaceState(null, "", `/admin/race-edit?id=${out.race.id}`);
      openRace(main, out.race, out.outcomes);
    } catch (err) {
      showUploadError(err);
    }
  });

  byId("n-manual").addEventListener("click", async () => {
    try {
      const created = await api("/races", {
        method: "POST", body: { race_type: typeSelect.value || "triathlon" },
      });
      location.href = `/admin/race-edit?id=${created.id}`;
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  });
}

// --- an existing race -------------------------------------------------------------------

/** @param {HTMLElement} main @param {any} data @param {Outcome[]} outcomes */
function openRace(main, data, outcomes) {
  race = data;
  raceId = String(data.id);
  main.innerHTML = `
    <div class="page-head">
      <h1 id="page-title">Race</h1>
      <a class="small" id="view-link" href="/races/${esc(raceId)}">View public page</a>
    </div>
    <section class="card files-card" id="files-section">
      <div class="files-head"><h2>Files</h2><span id="files-badge"></span></div>
      <div id="files-status"></div>
      ${dropzoneHtml("Drop more FIT files of this race here, or click to choose.")}
      <ul class="result-list" id="file-results" aria-live="polite"></ul>
      <div id="files-list"></div>
      <div id="recordings-switch"></div>
    </section>
    <div class="card" id="completeness-section"></div>

    <section class="section" id="meta-section"></section>
    <section class="section" id="result-section"></section>
    <section class="section" id="visibility-section"></section>
    <section class="section" id="category-section"></section>
    <section class="section" id="weather-section"></section>
    <section class="section" id="fueling-section"></section>
    <section class="section" id="gear-section"></section>
    <section class="section" id="photos-section"></section>
    <section class="section danger-zone" id="danger-section"></section>
  `;

  wireFiles();
  wireMeta();
  wireResult();
  wireVisibility();
  wireCategory();
  wireWeather();
  wireFueling();
  wireGear();
  wirePhotos();
  wireDanger();

  renderAll();
  renderOutcomes(outcomes);
}

/** After a change that reaches across sections (files, race type): redraw the page. */
function renderAll() {
  visibleFuelSegments = new Set([
    ...fuelDefaults(), ...race.fueling.map((/** @type {any} */ f) => f.segment),
  ]);
  syncFuelingDraft();
  byId("page-title").textContent = race.name || "Untitled race";
  renderFiles();
  renderCompleteness();
  renderMeta();
  renderResult();
  renderVisibility();
  renderCategory();
  renderWeather();
  renderFueling();
  renderGear();
  renderPhotos();
}

async function reloadRace() {
  race = await api(`/races/${raceId}`);
  renderAll();
}

// --- files ----------------------------------------------------------------------------------

/** @param {string} text */
function dropzoneHtml(text) {
  return `<div class="dropzone" id="dropzone" tabindex="0" role="button"
      aria-label="Drop FIT files or click to choose">
    <p>${esc(text)}</p>
    <p class="small">A multisport file, one file per leg, or a second device's recording of a leg.</p>
    <input type="file" id="file-input" multiple accept=".fit">
  </div>`;
}

/** @param {(files: File[]) => void} onFiles */
function wireDropzone(onFiles) {
  const zone = byId("dropzone");
  const input = /** @type {HTMLInputElement} */ (byId("file-input"));
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => {
    if (input.files?.length) onFiles(Array.from(input.files));
    input.value = "";
  });
  ["dragover", "dragenter"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => {
    const files = /** @type {DragEvent} */ (e).dataTransfer?.files;
    if (files?.length) onFiles(Array.from(files));
  });
}

/** @param {File[]} files */
function showUploading(files) {
  byId("file-results").innerHTML = files
    .map((f) => `<li class="result-line">${esc(f.name)}: uploading…</li>`).join("");
}

/** @param {unknown} err */
function showUploadError(err) {
  byId("file-results").innerHTML =
    `<li class="result-line failed">${esc(err instanceof Error ? err.message : String(err))}</li>`;
}

/** @param {Outcome[]} outcomes */
function renderOutcomes(outcomes) {
  byId("file-results").innerHTML = outcomes.map((o) => {
    const elsewhere = o.result === "duplicate" && o.race_id && String(o.race_id) !== raceId
      ? ` <a href="/admin/race-edit?id=${o.race_id}">Open that race</a>` : "";
    return `<li class="result-line ${esc(o.result)}">${esc(o.filename ?? "File")}:
      ${esc(o.message)}${elsewhere}</li>`;
  }).join("");
}

/** @param {string} raceType */
function typeName(raceType) {
  return raceType === "triathlon" ? "triathlon" : RACE_TYPE_LABELS[/** @type {"run"} */ (raceType)].toLowerCase();
}

/** @param {import("./types.js").Segment} seg */
function legHtml(seg) {
  /** @type {Recording[]} */
  const recs = (race.recordings ?? []).filter((/** @type {Recording} */ r) => r.segment === seg);
  const primary = recs.find((r) => r.is_primary);
  let state = "missing";
  let detail = "no recording";
  if (primary?.is_derived) {
    state = "derived";
    detail = `${duration(primary.elapsed_time)}, derived from the gap`;
  } else if (primary) {
    state = "recorded";
    const devices = recs.length > 1 ? ` · ${recs.length} recordings` : "";
    detail = `${duration(primary.elapsed_time)} · ${primary.device_key ?? "unknown device"}${devices}`;
  }
  const mark = { recorded: "✓", derived: "≈", missing: "✗" }[state];
  return `<li class="leg ${state}">
    <span class="leg-mark" aria-hidden="true">${mark}</span>
    <span class="leg-name">${esc(SEGMENT_LABELS[seg])}</span>
    <span class="leg-detail tnum">${esc(detail)}</span>
  </li>`;
}

function renderFiles() {
  const segments = raceSegments(race.race_type);
  /** @type {string[]} */
  const missing = race.missing_segments ?? [];
  const labels = missing.map((s) => SEGMENT_LABELS[/** @type {"swim"} */ (s)]).join(", ");

  byId("files-badge").innerHTML = !missing.length
    ? `<span class="status-badge complete">Complete</span>`
    : race.marked_complete
      ? `<span class="status-badge complete">Marked complete</span>`
      : `<span class="status-badge incomplete">Incomplete</span>`;

  const line = !missing.length
    ? `Every leg of this ${typeName(race.race_type)} has a recording.`
    : race.marked_complete
      ? `Missing: ${labels}. You marked the race complete anyway.`
      : `Missing: ${labels}. Drop the file below, or mark the race complete if it's lost.`;
  byId("files-status").innerHTML = `
    <div class="field-row">
      <label for="f-type">Race type</label>
      <select id="f-type">
        ${RACE_TYPES.map((t) => `<option value="${t}" ${race.race_type === t ? "selected" : ""}>
          ${esc(RACE_TYPE_LABELS[t])}</option>`).join("")}
      </select>
      <span class="field-status" id="f-type-status"></span>
    </div>
    <ol class="legs">${segments.map(legHtml).join("")}</ol>
    <p class="small muted">${esc(line)}</p>
    ${missing.length || race.marked_complete ? `
      <label class="field-row"><input type="checkbox" id="f-complete"
        ${race.marked_complete ? "checked" : ""}> Mark as complete anyway</label>
      <span class="field-status" id="f-complete-status"></span>` : ""}`;

  /** @type {RaceUpload[]} */
  const uploads = race.uploads ?? [];
  byId("files-list").innerHTML = uploads.length ? `<div class="table-wrap"><table class="file-table">
      <thead><tr>
        <th>File</th><th>Legs</th><th>Start</th><th class="num">Distance</th><th>Device</th><th></th>
      </tr></thead>
      <tbody>${uploads.map(uploadRow).join("")}</tbody>
    </table></div>`
    : `<p class="muted small">No files yet.</p>`;

  renderRecordings();
}

/** @param {RaceUpload} u */
function uploadRow(u) {
  const start = u.detected_start_time ? new Date(u.detected_start_time).toLocaleString() : DASH;
  let legs;
  if (u.status === "parsed") {
    legs = `<div class="segment-pick">
        <label for="pick-${u.id}" class="small">Leg</label>
        <select id="pick-${u.id}">
          <option value="">— pick —</option>
          ${raceSegments(race.race_type).map((s) =>
            `<option value="${s}">${esc(SEGMENT_LABELS[s])}</option>`).join("")}
        </select>
        <button type="button" data-assign="${u.id}">Add</button>
      </div>
      <div class="small muted">The file doesn't say which sport it is.</div>
      <div class="field-status" id="pick-${u.id}-status"></div>`;
  } else {
    legs = (race.recordings ?? [])
      .filter((/** @type {Recording} */ r) => r.upload_id === u.id)
      .map((/** @type {Recording} */ r) => esc(SEGMENT_LABELS[r.segment])
        + (r.is_primary ? "" : ` <span class="marker">not primary</span>`))
      .join(", ") || DASH;
  }
  return `<tr>
    <td>${esc(u.filename ?? DASH)}</td>
    <td>${legs}</td>
    <td class="tnum">${esc(start)}</td>
    <td class="num tnum">${esc(distance(u.detected_distance_m))}</td>
    <td>${esc(u.summary?.device_key ?? DASH)}</td>
    <td><button type="button" class="link" data-remove-upload="${u.id}">Remove</button></td>
  </tr>`;
}

function wireFiles() {
  wireDropzone(async (files) => {
    showUploading(files);
    const form = new FormData();
    for (const file of files) form.append("files", file);
    try {
      /** @type {{outcomes: Outcome[], race: any}} */
      const out = await api(`/races/${raceId}/uploads`, { method: "POST", form });
      race = out.race;
      renderAll();
      renderOutcomes(out.outcomes);
    } catch (err) {
      showUploadError(err);
    }
  });

  const section = byId("files-section");
  section.addEventListener("change", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    if (target.id === "f-type") {
      const statusEl = byId("f-type-status");
      saveField(`/races/${raceId}`, "PATCH", { race_type: target.value }, statusEl, renderAll)
        .then((ok) => { if (!ok) target.value = race.race_type; });
    } else if (target.id === "f-complete") {
      saveField(`/races/${raceId}`, "PATCH", { marked_complete: target.checked },
        byId("f-complete-status"), renderAll);
    } else if (target.type === "radio" && target.name.startsWith("primary-")) {
      const seg = target.name.replace("primary-", "");
      saveField(`/activities/${target.value}`, "PATCH", { is_primary: true },
        byId(`rec-status-${seg}`), renderAll);
    }
  });
  section.addEventListener("click", async (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const assignId = target.getAttribute("data-assign");
    if (assignId) {
      const select = /** @type {HTMLSelectElement} */ (byId(`pick-${assignId}`));
      const statusEl = byId(`pick-${assignId}-status`);
      if (!select.value) { setStatus(statusEl, "Pick a leg first.", true); return; }
      saveField(`/races/${raceId}/attach`, "POST", { segments: { [assignId]: select.value } },
        statusEl, renderAll);
      return;
    }
    const removeId = target.getAttribute("data-remove-upload");
    if (removeId) {
      const upload = (race.uploads ?? []).find((/** @type {RaceUpload} */ u) => String(u.id) === removeId);
      if (!confirm(`Remove ${upload?.filename ?? "this file"} and its recordings from the race? `
        + "This can't be undone.")) return;
      try {
        await api(`/uploads/${removeId}`, { method: "DELETE" });
        await reloadRace();
      } catch (err) {
        alert(err instanceof Error ? err.message : String(err));
      }
    }
  });
}

// --- shared save helper ------------------------------------------------------------------

/**
 * @param {string} path @param {string} method @param {any} body
 * @param {HTMLElement|null} statusEl @param {() => void} [after]
 * @returns {Promise<boolean>} whether the save went through
 */
async function saveField(path, method, body, statusEl, after) {
  if (statusEl) { statusEl.textContent = "Saving…"; statusEl.className = "field-status"; }
  try {
    const result = await api(path, { method, body });
    if (result) race = result;
    if (statusEl) { statusEl.textContent = "Saved"; statusEl.className = "field-status ok"; }
    after?.();
    renderCompleteness();
    return true;
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
    return false;
  }
}

function renderCompleteness() {
  const el = /** @type {HTMLElement} */ (document.getElementById("completeness-section"));
  const c = race.completeness ?? {};
  const labels = {
    recordings: "Recordings", official_result: "Official result", fueling: "Fueling",
    classification: "Classification",
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

/** The splits the form asks for. A single-sport race's one split is its total, so none. */
function resultSegments() {
  const segments = raceSegments(race.race_type);
  return segments.length > 1 ? segments : [];
}

/** @param {import("./types.js").Segment} seg */
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
  const el = byId("result-section");
  const r = race.official_result;
  const splits = resultSegments();
  const source = !r
    ? `<p class="small muted">Type it in, or add the race's files to start from the recorded times.</p>`
    : r.is_prefilled
      ? `<p class="small muted">Filled in from the recordings and kept up to date with them,
          until you change a value or confirm it.</p>`
      : "";
  // A calculated total stays out of the input: sending it back would turn it into a typed
  // one, and a typed total no longer follows the splits.
  const totalDerived = r?.total_is_derived;
  el.innerHTML = `<h2>Official result</h2>
    ${source}
    ${splits.length ? `<div class="split-grid">${splits.map(splitFieldHtml).join("")}</div>` : ""}
    <div class="field-row">
      <label for="or-total">Total</label>
      <input id="or-total" data-or-field="total_time"
        placeholder="${totalDerived ? esc(duration(r.total_time)) : "h:mm:ss"}"
        value="${r?.total_time != null && !totalDerived ? duration(r.total_time) : ""}">
      <span class="field-status" id="or-total-status"></span>
      ${totalDerived ? `<span class="small muted">calculated from splits</span>` : ""}
    </div>
    <p class="result-summary" id="or-summary"></p>
    <div class="fields">
      <div class="field"><label for="or-overall_place">Overall place</label>
        <input id="or-overall_place" type="text" inputmode="numeric" data-or-field="overall_place"
          value="${r?.overall_place ?? ""}"><span class="field-status" id="or-overall_place-status"></span></div>
      <div class="field"><label for="or-age_group_place">Age-group place</label>
        <input id="or-age_group_place" type="text" inputmode="numeric" data-or-field="age_group_place"
          value="${r?.age_group_place ?? ""}"><span class="field-status" id="or-age_group_place-status"></span></div>
    </div>
    <label class="field-row confirm-row"><input type="checkbox" id="or-confirmed"
      ${r?.is_confirmed ? "checked" : ""} ${r ? "" : "disabled"}>
      Confirmed: checked against the published results</label>
    <span class="field-status" id="or-confirmed-status"></span>`;
  updateResultLive();
}

function updateResultLive() {
  const summary = document.getElementById("or-summary");
  if (!summary) return;
  const totalInput = /** @type {HTMLInputElement} */ (byId("or-total"));
  const totalStr = totalInput.value.trim();
  const splits = resultSegments();
  if (totalStr || !splits.length) { summary.textContent = ""; summary.className = "result-summary"; return; }
  const missing = [];
  let sum = 0;
  let allValid = true;
  for (const seg of splits) {
    const input = /** @type {HTMLInputElement} */ (byId(`or-${seg}`));
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
  const el = byId("result-section");
  el.addEventListener("input", (e) => {
    if (/** @type {HTMLElement} */ (e.target).getAttribute?.("data-or-field")) updateResultLive();
  });
  el.addEventListener("change", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    if (target.id !== "or-confirmed") return;
    saveField(`/races/${raceId}/official-result`, "PUT", { is_confirmed: target.checked },
      byId("or-confirmed-status"), () => renderResult());
  });
  el.addEventListener("focusout", (e) => {
    const target = /** @type {HTMLInputElement} */ (e.target);
    const field = target.getAttribute?.("data-or-field");
    if (!field || target.disabled) return;
    // Tabbing through a prefilled result must not count as an edit (which would stop it
    // following the recordings), so only a changed value saves.
    if (target.value === target.defaultValue) return;
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
 * inline error on the offending field) if something typed can't be parsed.
 * @param {HTMLElement|null} fallbackStatusEl */
function collectResultForm(fallbackStatusEl) {
  /** @type {Record<string, number|null>} */
  const body = {};
  for (const field of [...resultSegments().map((s) => `${s}_time`), "total_time"]) {
    const input = /** @type {HTMLInputElement} */ (byId(`or-${field.replace("_time", "")}`));
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
    const input = /** @type {HTMLInputElement} */ (byId(`or-${field}`));
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
  // The categories are triathlon distances (classification.md).
  el.hidden = race.race_type !== "triathlon";
  if (el.hidden) { el.innerHTML = ""; return; }
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

/** Fuel is taken on the bike and the run; a single-sport race has its one leg. */
function fuelDefaults() {
  return race.race_type === "triathlon" ? ["bike", "run"] : [race.race_type];
}

function renderFueling() {
  const el = /** @type {HTMLElement} */ (document.getElementById("fueling-section"));
  const all = raceSegments(race.race_type);
  const hidden = all.filter((s) => !visibleFuelSegments.has(s));
  el.innerHTML = `<h2>Fueling</h2>
    ${[...visibleFuelSegments].sort((a, b) => all.indexOf(/** @type {any} */(a)) - all.indexOf(/** @type {any} */(b)))
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

/** The primary switch, for legs recorded by more than one device (grouping.md). */
function renderRecordings() {
  /** @type {Map<string, any[]>} */
  const bySegment = new Map();
  for (const r of race.recordings ?? []) {
    if (!bySegment.has(r.segment)) bySegment.set(r.segment, []);
    bySegment.get(r.segment).push(r);
  }
  const multi = [...bySegment.entries()].filter(([, list]) => list.length > 1);
  byId("recordings-switch").innerHTML = multi.length ? `<h3>Primary recording</h3>
    <p class="small muted">Every number shown for a leg comes from its primary recording.</p>
    ${multi.map(([seg, list]) => `<div>
      <strong>${esc(SEGMENT_LABELS[/** @type {"swim"} */ (seg)])}</strong>
      ${list.map((r) => `<label class="recording-switch">
        <input type="radio" name="primary-${seg}" value="${r.id}" ${r.is_primary ? "checked" : ""}>
        ${esc(r.device_key ?? `Recording ${r.id}`)}
        <span class="small muted tnum">${distance(r.distance_m)} · ${duration(r.elapsed_time)}</span>
      </label>`).join("")}
      <span class="field-status" id="rec-status-${seg}"></span>
    </div>`).join("")}` : "";
}

// --- danger zone ----------------------------------------------------------------------

function wireDanger() {
  const el = /** @type {HTMLElement} */ (document.getElementById("danger-section"));
  el.innerHTML = `<h2>Danger zone</h2>
    <p class="small muted">Deleting removes the race with its files, recordings, fueling and
      photos.</p>
    <button type="button" id="delete-race">Delete race</button>
    <span class="field-status" id="delete-status"></span>`;
  document.getElementById("delete-race")?.addEventListener("click", async () => {
    if (!confirm(`Delete "${race.name}" and its files? This can't be undone.`)) return;
    try {
      await api(`/races/${raceId}`, { method: "DELETE" });
      location.href = "/";
    } catch (err) {
      setStatus(document.getElementById("delete-status"), err instanceof Error ? err.message : String(err), true);
    }
  });
}

main();
