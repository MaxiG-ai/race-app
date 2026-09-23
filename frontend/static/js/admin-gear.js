// @ts-check
// Gear inventory page (docs/features/gear.md).

import { api } from "./api.js";
import { header, fail } from "./layout.js";
import { distance, esc, date, DASH } from "./format.js";

/**
 * @typedef {Object} GearOut
 * @property {number} id
 * @property {"bike"|"shoes"|"wetsuit"|"other"} kind
 * @property {string} name
 * @property {string|null} brand
 * @property {string|null} model
 * @property {string|null} purchased_at
 * @property {string|null} retired_at
 * @property {number} distance_offset_m
 * @property {string|null} notes
 * @property {number} distance_m
 * @property {{id: number, manufacturer: string, serial_number: string, label: string|null}[]} devices
 */
/** @typedef {{manufacturer: string, serial_number: string, label: string|null,
 *   activity_count: number, last_seen: string}} UnknownDevice */

/** @type {GearOut[]} */
let inventory = [];
/** @type {UnknownDevice[]} */
let unknown = [];

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
    <h1>Gear</h1>

    <section class="section" id="unknown-section">
      <h2>New sensors seen</h2>
      <div id="unknown-list"></div>
    </section>

    <section class="section">
      <h2>Add gear</h2>
      <form id="new-gear" class="fields">
        <div class="field">
          <label for="g-kind">Kind</label>
          <select id="g-kind" required>
            <option value="bike">Bike</option>
            <option value="shoes">Shoes</option>
            <option value="wetsuit">Wetsuit</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="field"><label for="g-name">Name</label><input id="g-name" required></div>
        <div class="field"><label for="g-brand">Brand</label><input id="g-brand"></div>
        <div class="field"><label for="g-model">Model</label><input id="g-model"></div>
        <div class="field"><label for="g-purchased">Purchased</label><input id="g-purchased" type="date"></div>
        <div class="field"><label for="g-offset">Distance before tracking (m)</label>
          <input id="g-offset" type="number" min="0" step="1" value="0"></div>
      </form>
      <button type="submit" form="new-gear" id="g-submit">Add gear</button>
      <span class="field-status" id="new-gear-status"></span>
    </section>

    <section class="section">
      <h2>Active</h2>
      <div id="active-list"></div>
    </section>
    <section class="section">
      <h2>Retired</h2>
      <div id="retired-list"></div>
    </section>
  `;

  document.getElementById("new-gear")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const statusEl = /** @type {HTMLElement} */ (document.getElementById("new-gear-status"));
    const name = /** @type {HTMLInputElement} */ (document.getElementById("g-name")).value.trim();
    if (!name) { statusEl.textContent = "Name is required."; statusEl.className = "field-status err"; return; }
    const body = {
      kind: /** @type {HTMLSelectElement} */ (document.getElementById("g-kind")).value,
      name,
      brand: /** @type {HTMLInputElement} */ (document.getElementById("g-brand")).value.trim() || null,
      model: /** @type {HTMLInputElement} */ (document.getElementById("g-model")).value.trim() || null,
      purchased_at: /** @type {HTMLInputElement} */ (document.getElementById("g-purchased")).value || null,
      distance_offset_m: Number(/** @type {HTMLInputElement} */ (document.getElementById("g-offset")).value || 0),
    };
    try {
      await api("/gear", { method: "POST", body });
      /** @type {HTMLFormElement} */ (document.getElementById("new-gear")).reset();
      statusEl.textContent = "Added.";
      statusEl.className = "field-status ok";
      await loadGear();
    } catch (err) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
  });

  document.getElementById("active-list")?.addEventListener("click", onGearAction);
  document.getElementById("retired-list")?.addEventListener("click", onGearAction);
  document.getElementById("unknown-list")?.addEventListener("click", onUnknownAction);

  await loadGear();
}

async function loadGear() {
  try {
    const [gear, unknownDevices] = await Promise.all([api("/gear"), api("/gear/unknown-devices")]);
    inventory = gear;
    unknown = unknownDevices;
    renderInventory();
    renderUnknown();
  } catch (err) {
    fail(/** @type {HTMLElement} */ (document.getElementById("active-list")), err);
  }
}

function renderInventory() {
  const active = inventory.filter((g) => !g.retired_at);
  const retired = inventory.filter((g) => g.retired_at);
  document.getElementById("active-list").innerHTML = active.length
    ? active.map(gearRow).join("") : `<p class="muted small">No gear yet.</p>`;
  document.getElementById("retired-list").innerHTML = retired.length
    ? retired.map(gearRow).join("") : `<p class="muted small">Nothing retired.</p>`;
}

/** @param {GearOut} g */
function gearRow(g) {
  const retired = !!g.retired_at;
  return `<div class="gear-row${retired ? " retired" : ""}" data-gear="${g.id}">
    <input class="name" data-rename="${g.id}" value="${esc(g.name)}" aria-label="Name">
    <span class="badge">${esc(g.kind)}</span>
    <span class="tnum small muted">${esc(distance(g.distance_m))}</span>
    ${g.devices.length ? `<span class="small muted">${esc(g.devices.map((d) => d.label || d.serial_number).join(", "))}</span>` : ""}
    ${retired ? `<span class="small muted">retired ${esc(date(g.retired_at))}</span>
      <button type="button" data-unretire="${g.id}">Unretire</button>`
      : `<button type="button" data-retire="${g.id}">Retire</button>`}
    <span class="field-status" id="gear-status-${g.id}"></span>
  </div>`;
}

/** @param {MouseEvent} e */
async function onGearAction(e) {
  const target = /** @type {HTMLElement} */ (e.target);
  const retireId = target.getAttribute("data-retire");
  const unretireId = target.getAttribute("data-unretire");
  if (retireId) {
    await patchGear(Number(retireId), { retired_at: new Date().toISOString().slice(0, 10) });
  } else if (unretireId) {
    await patchGear(Number(unretireId), { retired_at: null });
  }
}

/** Rename saves on blur via a delegated focusout listener set up once. */
document.addEventListener("focusout", (e) => {
  const target = /** @type {HTMLElement} */ (e.target);
  const id = target.getAttribute?.("data-rename");
  if (!id) return;
  const value = /** @type {HTMLInputElement} */ (target).value.trim();
  if (!value) return;
  patchGear(Number(id), { name: value });
});

/** @param {number} id @param {Record<string, any>} body */
async function patchGear(id, body) {
  const statusEl = document.getElementById(`gear-status-${id}`);
  try {
    await api(`/gear/${id}`, { method: "PATCH", body });
    if (statusEl) { statusEl.textContent = "Saved"; statusEl.className = "field-status ok"; }
    await loadGear();
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err instanceof Error ? err.message : String(err);
      statusEl.className = "field-status err";
    }
  }
}

function renderUnknown() {
  const section = /** @type {HTMLElement} */ (document.getElementById("unknown-section"));
  const list = /** @type {HTMLElement} */ (document.getElementById("unknown-list"));
  section.classList.toggle("hidden", unknown.length === 0);
  const gearOptions = inventory.filter((g) => !g.retired_at)
    .map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("");
  list.innerHTML = unknown.map((d, i) => `
    <div class="unknown-device" data-unknown="${i}">
      <span class="meta">${esc(d.manufacturer)} · ${esc(d.serial_number)}<br>
        <span class="muted small">${d.activity_count} recording${d.activity_count === 1 ? "" : "s"},
          last seen ${esc(date(d.last_seen.slice(0, 10)))}</span></span>
      <label class="small" for="unknown-gear-${i}">Which gear is this?</label>
      <select id="unknown-gear-${i}">
        <option value="">— pick —</option>
        ${gearOptions}
      </select>
      <input type="text" id="unknown-label-${i}" placeholder="Label (optional)" style="width:9rem">
      <button type="button" data-assign="${i}">Assign</button>
      <span class="field-status" id="unknown-status-${i}"></span>
    </div>`).join("");
}

/** @param {MouseEvent} e */
async function onUnknownAction(e) {
  const target = /** @type {HTMLElement} */ (e.target);
  const idx = target.getAttribute("data-assign");
  if (idx === null) return;
  const i = Number(idx);
  const d = unknown[i];
  const gearId = /** @type {HTMLSelectElement} */ (document.getElementById(`unknown-gear-${i}`)).value;
  const label = /** @type {HTMLInputElement} */ (document.getElementById(`unknown-label-${i}`)).value.trim();
  const statusEl = /** @type {HTMLElement} */ (document.getElementById(`unknown-status-${i}`));
  if (!gearId) { statusEl.textContent = "Pick a gear item first."; statusEl.className = "field-status err"; return; }
  try {
    await api(`/gear/${gearId}/devices`, {
      method: "POST",
      body: { manufacturer: d.manufacturer, serial_number: d.serial_number, label: label || null },
    });
    await loadGear();
  } catch (err) {
    statusEl.textContent = err instanceof Error ? err.message : String(err);
    statusEl.className = "field-status err";
  }
}

main();
