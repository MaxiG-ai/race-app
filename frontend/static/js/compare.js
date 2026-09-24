// @ts-check
// Comparison page: /compare?a=&b=. docs/features/comparison.md.

import { api, ApiError } from "./api.js";
import { header, tableFallback, fail } from "./layout.js";
import { DASH, SEGMENTS, SEGMENT_LABELS, duration, delta, distance, elevation, esc, date } from "./format.js";
import { gapChart } from "./charts.js";

const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const params = new URLSearchParams(location.search);
const aId = Number(params.get("a"));
const bId = params.get("b") ? Number(params.get("b")) : null;

let isOwner = false;

async function main_() {
  const who = await header();
  isOwner = who.owner;

  if (!Number.isFinite(aId)) {
    main.innerHTML = `<p class="error">Choose a race to compare from its detail page.</p>`;
    return;
  }

  if (bId == null || !Number.isFinite(bId)) {
    await renderPicker();
    return;
  }

  try {
    const cmp = await api(`/races/${aId}/compare/${bId}`);
    renderComparison(cmp);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      main.innerHTML = `<p class="error">Race not found.</p>`;
      return;
    }
    if (err instanceof ApiError && err.status === 422) {
      await renderPicker(err.message);
      return;
    }
    fail(main, err);
  }
}

/** @param {string} [note] */
async function renderPicker(note) {
  /** @type {import("./types.js").RaceDetail} */
  let race;
  /** @type {import("./types.js").RaceListItem[]} */
  let comparable = [];
  try {
    [race, comparable] = await Promise.all([
      api(`/races/${aId}`),
      api(`/races/${aId}/comparable`),
    ]);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      main.innerHTML = `<p class="error">Race not found.</p>`;
      return;
    }
    fail(main, err);
    return;
  }

  main.innerHTML = `
    <h1>Compare ${esc(race.name)}</h1>
    ${note ? `<p class="error">${esc(note)}</p>` : ""}
    ${comparable.length ? `
      <ul class="picker-list">
        ${comparable.map((r) => `<li>
          <a href="/compare?a=${aId}&b=${r.id}">
            <span>${esc(r.name)}</span>
            <span class="muted">${esc(date(r.date))}</span>
            <span class="tnum">${esc(duration(r.total_time))}</span>
          </a>
        </li>`).join("")}
      </ul>
    ` : `<p class="muted">No other race in this category yet.</p>`}
  `;
}

/** @param {import("./types.js").Comparison} cmp */
function renderComparison(cmp) {
  const { a, b } = cmp;
  const finish = [...cmp.checkpoints].reverse().find((c) => c.gap_s !== null);
  const totalGap = finish?.gap_s ?? (a.total_time != null && b.total_time != null
    ? b.total_time - a.total_time : null);

  const deltaStrip = SEGMENTS.map((s) =>
    `<span class="cmp-delta-item"><span class="muted">${esc(SEGMENT_LABELS[s])}</span>
      <span class="tnum">${esc(delta(cmp.deltas[s]))}</span></span>`).join("");

  const notes = [];
  if (cmp.normalised) notes.push("checkpoints are fractions of each leg: the distances differ");
  if (cmp.coarse) notes.push("from official splits only");

  const checkpointCols = ["Checkpoint", a.name, b.name, "Gap"];
  const checkpointRows = cmp.checkpoints.map((c) =>
    [c.label, duration(c.a), duration(c.b), delta(c.gap_s)]);

  main.innerHTML = `
    <div class="cmp-header">
      <div class="cmp-race">
        <h1>${esc(a.name)}</h1>
        <p class="muted">${esc(date(a.date))}</p>
        <p class="cmp-total tnum">${esc(duration(a.total_time))}</p>
      </div>
      <div class="cmp-race">
        <h1>${esc(b.name)}</h1>
        <p class="muted">${esc(date(b.date))}</p>
        <p class="cmp-total tnum">${esc(duration(b.total_time))}
          <span class="cmp-gap">${esc(delta(totalGap))}</span></p>
      </div>
    </div>

    <div class="cmp-delta-strip">${deltaStrip}</div>

    <section>
      <h2>Gap to reference</h2>
      ${notes.length ? `<p class="muted small">${notes.map(esc).join(" · ")}</p>` : ""}
      <div class="chart"><canvas id="gap-canvas"></canvas></div>
      ${tableFallback("Checkpoint splits", checkpointCols, checkpointRows)}
      <p><button type="button" id="swap-btn">Swap reference</button></p>
    </section>

    <section id="cmp-conditions"></section>
    <section id="cmp-fueling"></section>
  `;

  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("gap-canvas"));
  gapChart(canvas, cmp, { referenceName: a.name, otherName: b.name });

  const swap = /** @type {HTMLButtonElement} */ (document.getElementById("swap-btn"));
  swap.addEventListener("click", () => { location.href = `/compare?a=${b.id}&b=${a.id}`; });

  renderConditions(cmp);
  renderFueling(cmp);
}

/** @param {import("./types.js").Comparison} cmp */
function renderConditions(cmp) {
  const items = cmp.conditions.filter((c) => isOwner || !c.field.endsWith("_device"));
  items.sort((x, y) => Number(y.material) - Number(x.material));
  const section = /** @type {HTMLElement} */ (document.getElementById("cmp-conditions"));
  if (!items.length) {
    section.innerHTML = `<h2>Conditions &amp; course</h2><p class="muted">Nothing differs.</p>`;
    return;
  }
  section.innerHTML = `
    <h2>Conditions &amp; course</h2>
    <ul class="cmp-conditions-list">
      ${items.map((item) => `<li class="${item.material ? "material" : ""}">
        ${item.material ? "&#9888; " : ""}${esc(formatCondition(item))}
      </li>`).join("")}
    </ul>
  `;
}

/** @param {{field: string, a: any, b: any, material: boolean, delta?: number, delta_pct?: number}} item */
function formatCondition(item) {
  const f = item.field;
  if (f.endsWith("_distance_m")) {
    const sport = f.replace("_distance_m", "");
    return `${SEGMENT_LABELS[sport] ?? sport} ${distance(item.a)} vs ${distance(item.b)} (${signedPct(item.delta_pct)})`;
  }
  if (f === "elevation_gain_m") {
    const ea = elevation(item.a), eb = elevation(item.b);
    return `${ea === DASH ? ea : `+${ea}`} vs ${eb === DASH ? eb : `+${eb}`} elevation gain`;
  }
  if (f === "temp_c") return `${Math.round(item.a)} vs ${Math.round(item.b)} °C`;
  if (f === "humidity_pct") return `${Math.round(item.a)} vs ${Math.round(item.b)} % humidity`;
  if (f.endsWith("_gear")) {
    const seg = f.replace("_gear", "");
    return `${SEGMENT_LABELS[seg] ?? seg} gear: ${item.a ?? DASH} vs ${item.b ?? DASH}`;
  }
  if (f.endsWith("_device")) {
    const seg = f.replace("_device", "");
    return `${SEGMENT_LABELS[seg] ?? seg} device: ${item.a ?? DASH} vs ${item.b ?? DASH}`;
  }
  return `${f}: ${item.a ?? DASH} vs ${item.b ?? DASH}`;
}

/** @param {number|undefined} v */
function signedPct(v) {
  if (v == null) return DASH;
  return `${v > 0 ? "+" : ""}${Math.round(v)} %`;
}

/** @param {import("./types.js").Comparison} cmp */
function renderFueling(cmp) {
  const section = /** @type {HTMLElement} */ (document.getElementById("cmp-fueling"));
  if (!cmp.fueling.length) {
    section.innerHTML = `<h2>Fueling</h2><p class="muted">No fueling recorded.</p>`;
    return;
  }
  const rows = cmp.fueling.map((f) => {
    const aProducts = productsList(f.a?.products ?? [], new Set(f.only_a));
    const bProducts = productsList(f.b?.products ?? [], new Set(f.only_b));
    return `<tr>
      <td>${esc(SEGMENT_LABELS[f.segment] ?? f.segment)}</td>
      <td class="num tnum">${esc(carbsCell(f.a))}</td>
      <td class="num tnum">${esc(carbsCell(f.b))}</td>
      <td class="num tnum">${esc(f.carbs_delta_g === null ? DASH : `${f.carbs_delta_g > 0 ? "+" : ""}${f.carbs_delta_g} g`)}</td>
      <td>${aProducts}</td>
      <td>${bProducts}</td>
    </tr>`;
  }).join("");
  section.innerHTML = `
    <h2>Fueling</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Segment</th><th class="num">Carbs / g/h (A)</th><th class="num">Carbs / g/h (B)</th>
        <th class="num">&Delta; carbs</th><th>Products A</th><th>Products B</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  `;
}

/** @param {{carbs_g: number|null, g_per_h: number|null}|null} side */
function carbsCell(side) {
  if (!side) return DASH;
  const carbs = side.carbs_g != null ? `${side.carbs_g} g` : DASH;
  const gph = side.g_per_h != null ? `${side.g_per_h} g/h` : DASH;
  return `${carbs} / ${gph}`;
}

/** @param {{name: string, quantity: number|null}[]} products @param {Set<string>} unique */
function productsList(products, unique) {
  if (!products.length) return DASH;
  return products.map((p) => {
    const text = p.quantity != null ? `${esc(p.name)} &times;${esc(p.quantity)}` : esc(p.name);
    return unique.has(p.name) ? `<strong class="unique-item">${text}</strong>` : text;
  }).join(", ");
}

main_().catch((err) => fail(main, err));
