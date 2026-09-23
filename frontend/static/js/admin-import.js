// @ts-check
// Import page: drop zone for FIT files + the grouping tray (docs/features/import.md,
// docs/features/grouping.md).

import { api } from "./api.js";
import { header, fail } from "./layout.js";
import { distance, duration, esc, DASH } from "./format.js";

/**
 * @typedef {Object} UploadOut
 * @property {number} id
 * @property {string|null} filename
 * @property {"parsed"|"grouped"|"failed"} status
 * @property {"multisport"|"single_sport"|null} kind
 * @property {"swim"|"bike"|"run"|"other"|null} detected_sport
 * @property {string|null} detected_start_time
 * @property {string|null} detected_end_time
 * @property {number|null} detected_distance_m
 * @property {Record<string, any>|null} summary
 * @property {string|null} error
 * @property {number|null} race_id
 * @property {string} uploaded_at
 */
/** @typedef {{upload_ids: number[], segments: Record<string, string>, ambiguous: boolean}} Proposal */
/** @typedef {{filename: string|null, result: string, message: string, upload_id: number|null, race_id: number|null}} Outcome */

/** @type {UploadOut[]} */
let trayUploads = [];
/** @type {Proposal[]} */
let proposals = [];
/** @type {Set<number>} */
const selected = new Set();
/** @type {Record<number, string>} */
const segmentChoices = {};

/** @type {import("./types.js").RaceListItem[]|null} */
let racesCache = null;

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
    location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    return;
  }

  main.innerHTML = `
    <div class="page-head">
      <h1>Import</h1>
      <button type="button" id="new-race">New race</button>
    </div>
    <div class="dropzone" id="dropzone" tabindex="0" role="button" aria-label="Drop FIT files or click to choose">
      <p>Drop FIT files here, or click to choose.</p>
      <p class="small">Multiple files at once are fine.</p>
      <input type="file" id="file-input" multiple accept=".fit">
    </div>
    <ul class="result-list" id="results" aria-live="polite"></ul>

    <h2>Grouping tray</h2>
    <p class="muted small" id="tray-empty" class="hidden">Nothing waiting to be grouped.</p>
    <div id="proposals"></div>
    <div class="table-wrap"><table id="tray-table">
      <thead><tr>
        <th></th><th>Sport</th><th>Start</th><th class="num">Distance</th><th>Device</th>
        <th>Filename</th><th></th><th></th>
      </tr></thead>
      <tbody id="tray-body"></tbody>
    </table></div>
    <div class="tray-actions">
      <button type="button" id="create-race" disabled>Create race</button>
      <button type="button" id="attach-toggle">Add to an existing race…</button>
      <span class="field-status" id="tray-status"></span>
    </div>
    <div class="attach-panel hidden" id="attach-panel">
      <label for="attach-select">Race</label>
      <select id="attach-select"></select>
      <button type="button" id="attach-confirm">Attach</button>
    </div>
  `;

  document.getElementById("new-race")?.addEventListener("click", async () => {
    try {
      const race = await api("/races", { method: "POST", body: {} });
      location.href = `/admin/race-edit?id=${race.id}`;
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  });

  setupDropzone();
  setupTrayActions();
  await loadTray();
}

// --- drop zone / upload ---------------------------------------------------------------

function setupDropzone() {
  const zone = /** @type {HTMLElement} */ (document.getElementById("dropzone"));
  const input = /** @type {HTMLInputElement} */ (document.getElementById("file-input"));
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => {
    if (input.files?.length) handleFiles(input.files);
    input.value = "";
  });
  ["dragover", "dragenter"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => {
    const files = e.dataTransfer?.files;
    if (files?.length) handleFiles(files);
  });
}

/** @param {FileList} files */
async function handleFiles(files) {
  const list = /** @type {HTMLElement} */ (document.getElementById("results"));
  for (const file of Array.from(files)) {
    const li = document.createElement("li");
    li.className = "result-line";
    li.textContent = `${file.name}: uploading…`;
    list.appendChild(li);
    try {
      const form = new FormData();
      form.append("files", file);
      /** @type {Outcome[]} */
      const outcomes = await api("/uploads", { method: "POST", form });
      renderOutcome(li, outcomes[0] ?? { filename: file.name, result: "failed", message: "No response.", upload_id: null, race_id: null });
    } catch (err) {
      renderOutcome(li, {
        filename: file.name, result: "failed",
        message: err instanceof Error ? err.message : String(err), upload_id: null, race_id: null,
      });
    }
  }
  await loadTray();
}

/** @param {HTMLElement} el @param {Outcome} outcome */
function renderOutcome(el, outcome) {
  el.className = `result-line ${outcome.result}`;
  const name = esc(outcome.filename ?? "File");
  if (outcome.result === "created_race" && outcome.race_id) {
    el.innerHTML = `${name}: <a href="/races/${outcome.race_id}">Created race — open it</a>`;
  } else if (outcome.result === "in_tray") {
    el.textContent = `${outcome.filename ?? "File"}: in tray, ready to group.`;
  } else {
    el.textContent = `${outcome.filename ?? "File"}: ${outcome.message}`;
  }
}

// --- tray -------------------------------------------------------------------------------

async function loadTray() {
  try {
    /** @type {{uploads: UploadOut[], proposals: Proposal[]}} */
    const tray = await api("/uploads?status=parsed");
    trayUploads = tray.uploads;
    proposals = tray.proposals;
    const ids = new Set(trayUploads.map((u) => u.id));
    for (const id of [...selected]) if (!ids.has(id)) selected.delete(id);
    renderTray();
  } catch (err) {
    fail(/** @type {HTMLElement} */ (document.getElementById("tray-body")), err);
  }
}

function renderTray() {
  const emptyEl = /** @type {HTMLElement} */ (document.getElementById("tray-empty"));
  emptyEl.classList.toggle("hidden", trayUploads.length > 0);

  const needing = idsNeedingPicker();

  const proposalsEl = /** @type {HTMLElement} */ (document.getElementById("proposals"));
  proposalsEl.innerHTML = proposals.map((p, i) => {
    const names = p.upload_ids
      .map((id) => trayUploads.find((u) => u.id === id)?.filename ?? `upload ${id}`)
      .join(", ");
    return `<div class="proposal">
      <span>Suggested: these ${p.upload_ids.length} look like one race — ${esc(names)}${p.ambiguous ? " (order unclear)" : ""}</span>
      <button type="button" data-select-proposal="${i}">Select</button>
    </div>`;
  }).join("");

  const body = /** @type {HTMLElement} */ (document.getElementById("tray-body"));
  body.innerHTML = trayUploads.map((u) => {
    const isSelected = selected.has(u.id);
    const device = u.summary?.device_key ?? DASH;
    const start = u.detected_start_time ? new Date(u.detected_start_time).toLocaleString() : DASH;
    const sport = u.kind === "multisport" ? "multisport" : (u.detected_sport ?? DASH);
    const picker = needing.has(u.id) ? `
      <div class="segment-pick">
        <label for="seg-${u.id}" class="small">Segment</label>
        <select id="seg-${u.id}" data-segment-for="${u.id}">
          <option value="">— pick —</option>
          ${["swim", "t1", "bike", "t2", "run"].map((s) =>
            `<option value="${s}" ${segmentChoices[u.id] === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </div>` : "";
    return `<tr class="tray-row${isSelected ? " selected" : ""}" data-upload-row="${u.id}">
      <td><input type="checkbox" data-select="${u.id}" ${isSelected ? "checked" : ""}
        aria-label="Select ${esc(u.filename ?? "upload")}"></td>
      <td>${esc(sport)}</td>
      <td class="tnum">${esc(start)}</td>
      <td class="num tnum">${esc(distance(u.detected_distance_m))}</td>
      <td>${esc(device)}</td>
      <td>${esc(u.filename ?? DASH)}${u.error ? `<div class="small muted">${esc(u.error)}</div>` : ""}</td>
      <td>${picker}</td>
      <td><button type="button" class="link" data-delete="${u.id}">Delete</button></td>
    </tr>`;
  }).join("");

  updateCreateButton();
}

/** Rows that need an explicit segment before a race can be created from them. */
function idsNeedingPicker() {
  /** @type {Set<number>} */
  const needs = new Set();
  const chosen = trayUploads.filter((u) => selected.has(u.id));
  for (const u of chosen) {
    if (u.kind !== "multisport" && (!u.detected_sport || u.detected_sport === "other")) needs.add(u.id);
  }
  /** @type {Map<string, UploadOut[]>} */
  const bySport = new Map();
  for (const u of chosen) {
    if (u.kind === "multisport" || !u.detected_sport) continue;
    const list = bySport.get(u.detected_sport) ?? [];
    list.push(u);
    bySport.set(u.detected_sport, list);
  }
  for (const list of bySport.values()) if (list.length > 1) for (const u of list) needs.add(u.id);
  for (const p of proposals) {
    if (p.ambiguous && p.upload_ids.length === selected.size && p.upload_ids.every((id) => selected.has(id))) {
      for (const id of p.upload_ids) needs.add(id);
    }
  }
  return needs;
}

function updateCreateButton() {
  const btn = /** @type {HTMLButtonElement} */ (document.getElementById("create-race"));
  const needing = idsNeedingPicker();
  const allPicked = [...needing].every((id) => segmentChoices[id]);
  btn.disabled = selected.size === 0 || !allPicked;
}

function currentSegments() {
  /** @type {Record<string, string>} */
  const segments = {};
  for (const id of selected) if (segmentChoices[id]) segments[id] = segmentChoices[id];
  return segments;
}

function setupTrayActions() {
  const body = /** @type {HTMLElement} */ (document.getElementById("tray-body"));
  body.addEventListener("change", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const checkboxId = target.getAttribute("data-select");
    if (checkboxId) {
      const id = Number(checkboxId);
      if (/** @type {HTMLInputElement} */ (target).checked) selected.add(id); else selected.delete(id);
      renderTray();
      return;
    }
    const segFor = target.getAttribute("data-segment-for");
    if (segFor) {
      const id = Number(segFor);
      const value = /** @type {HTMLSelectElement} */ (target).value;
      if (value) segmentChoices[id] = value; else delete segmentChoices[id];
      updateCreateButton();
    }
  });
  body.addEventListener("click", async (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const delId = target.getAttribute("data-delete");
    if (delId) {
      const upload = trayUploads.find((u) => u.id === Number(delId));
      if (!confirm(`Delete ${upload?.filename ?? "this upload"}? This can't be undone.`)) return;
      try {
        await api(`/uploads/${delId}`, { method: "DELETE" });
        selected.delete(Number(delId));
        await loadTray();
      } catch (err) {
        alert(err instanceof Error ? err.message : String(err));
      }
    }
  });

  document.getElementById("proposals")?.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    const idx = target.getAttribute("data-select-proposal");
    if (idx === null) return;
    const proposal = proposals[Number(idx)];
    if (!proposal) return;
    selected.clear();
    for (const id of proposal.upload_ids) selected.add(id);
    for (const key of Object.keys(segmentChoices)) delete segmentChoices[Number(key)];
    for (const [id, seg] of Object.entries(proposal.segments)) segmentChoices[Number(id)] = seg;
    renderTray();
  });

  document.getElementById("create-race")?.addEventListener("click", async () => {
    const statusEl = /** @type {HTMLElement} */ (document.getElementById("tray-status"));
    statusEl.textContent = "";
    statusEl.className = "field-status";
    try {
      const race = await api("/races/group", {
        method: "POST",
        body: { upload_ids: [...selected], segments: currentSegments() },
      });
      location.href = `/admin/race-edit?id=${race.id}`;
    } catch (err) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
  });

  document.getElementById("attach-toggle")?.addEventListener("click", async () => {
    const panel = /** @type {HTMLElement} */ (document.getElementById("attach-panel"));
    panel.classList.toggle("hidden");
    if (panel.classList.contains("hidden")) return;
    const select = /** @type {HTMLSelectElement} */ (document.getElementById("attach-select"));
    if (!racesCache) {
      try {
        const list = await api("/races?limit=200");
        racesCache = list.items;
      } catch {
        racesCache = [];
      }
    }
    select.innerHTML = racesCache
      .map((r) => `<option value="${r.id}">${esc(r.date)} — ${esc(r.name)}</option>`).join("")
      || `<option value="">No races yet</option>`;
  });

  document.getElementById("attach-confirm")?.addEventListener("click", async () => {
    const select = /** @type {HTMLSelectElement} */ (document.getElementById("attach-select"));
    const statusEl = /** @type {HTMLElement} */ (document.getElementById("tray-status"));
    const raceId = select.value;
    if (!raceId) return;
    statusEl.textContent = "";
    statusEl.className = "field-status";
    try {
      await api(`/races/${raceId}/attach`, {
        method: "POST",
        body: { upload_ids: [...selected], segments: currentSegments() },
      });
      location.href = `/admin/race-edit?id=${raceId}`;
    } catch (err) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
  });
}

main();
