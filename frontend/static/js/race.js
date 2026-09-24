// @ts-check
// Race detail page: /races/{id}. docs/features/race-detail.md.

import { api, ApiError } from "./api.js";
import { header, segmentBar, tableFallback, fail } from "./layout.js";
import {
  DASH, SEGMENTS, SPORTS, SEGMENT_LABELS, duration, delta, distance, elevation,
  pace, paceValue, number, date as fmtDate, esc,
} from "./format.js";
import { lineChart, token } from "./charts.js";

const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const id = Number(location.pathname.split("/").pop());

/** @type {any} */
let paceChart = null;
/** @type {import("./types.js").RaceDetail | null} */
let race = null;
let isOwner = false;
/** @type {import("./types.js").Lap[]} */
let allLaps = [];
let activeSport = /** @type {string|null} */ (null);

async function main_() {
  const who = await header({ edit: `/admin/race-edit?id=${id}` });
  isOwner = who.owner;

  if (!Number.isFinite(id)) {
    main.innerHTML = `<p class="error">Race not found.</p>`;
    return;
  }

  try {
    race = await api(`/races/${id}`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      main.innerHTML = `<p class="error">Race not found.</p>`;
      return;
    }
    fail(main, err);
    return;
  }

  main.innerHTML = skeleton();
  renderHeader();
  renderSplits();
  renderConditions();
  renderNotes();
  renderRecordings();

  // Track and laps load independently; a failure in one shouldn't blank the page.
  loadTrack();
  loadLaps();
}

function skeleton() {
  return `
    <div id="race-header"></div>
    <section id="race-splits"></section>
    <section id="race-map">
      <h2>Course</h2>
      <div id="map-body"></div>
    </section>
    <section id="race-pacing">
      <h2>Pacing</h2>
      <div id="pacing-body"><p class="muted">Loading…</p></div>
    </section>
    <section id="race-conditions"></section>
    <section id="race-notes"></section>
    <section id="race-recordings"></section>
  `;
}

function renderHeader() {
  const r = /** @type {import("./types.js").RaceDetail} */ (race);
  const official = r.official_result;
  const bySegment = /** @type {Record<string, import("./types.js").SegmentOut>} */ ({});
  for (const s of r.segments) bySegment[s.segment] = s;

  const officialComplete = !!official && SEGMENTS.every((s) => official[`${s}_time`] != null);
  let times, derivedList;
  if (officialComplete && official) {
    times = /** @type {Record<string, number|null>} */ ({});
    for (const s of SEGMENTS) times[s] = official[`${s}_time`];
    derivedList = official.derived_split ? [official.derived_split] : [];
  } else {
    times = /** @type {Record<string, number|null>} */ ({});
    for (const s of SEGMENTS) times[s] = bySegment[s]?.elapsed_time ?? null;
    derivedList = r.segments.filter((s) => s.is_derived).map((s) => s.segment);
  }

  const recordedComplete = SEGMENTS.every((s) => bySegment[s]?.elapsed_time != null);
  const recordedTotal = recordedComplete
    ? SEGMENTS.reduce((sum, s) => sum + /** @type {number} */ (bySegment[s].elapsed_time), 0)
    : null;
  const totalTime = official?.total_time ?? recordedTotal;

  const meta = [fmtDate(r.date), r.location_name].filter(Boolean).join(" · ");
  document.getElementById("race-header").innerHTML = `
    <h1>${esc(r.name)}</h1>
    <p class="race-meta">
      ${esc(meta)}
      ${r.category ? `<span class="badge">${esc(r.category.label)}</span>` : ""}
    </p>
    <p class="race-total tnum">${esc(duration(totalTime))}</p>
    ${segmentBar(times, derivedList)}
    <p class="race-actions"><a class="button" href="/compare?a=${id}">Compare</a></p>
  `;
}

function renderSplits() {
  const r = /** @type {import("./types.js").RaceDetail} */ (race);
  const official = r.official_result;
  const bySegment = /** @type {Record<string, import("./types.js").SegmentOut>} */ ({});
  for (const s of r.segments) bySegment[s.segment] = s;

  const rows = SEGMENTS.map((seg) => rowFor(seg));
  rows.push(totalRow());

  document.getElementById("race-splits").innerHTML = `
    <h2>Splits</h2>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Segment</th><th class="num">Official</th><th class="num">Recorded</th>
        <th class="num">&Delta;</th><th class="num">Distance</th><th class="num">Pace</th>
      </tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>
  `;

  function rowFor(/** @type {import("./types.js").Segment} */ seg) {
    const rec = bySegment[seg];
    const officialTime = official ? official[`${seg}_time`] : null;
    const recordedTime = rec?.elapsed_time ?? null;
    const d = officialTime != null && recordedTime != null ? recordedTime - officialTime : null;
    const officialMark = official?.derived_split === seg
      ? ` <span class="marker">calculated</span>` : "";
    const recordedMark = rec?.is_derived
      ? ` <span class="marker">derived from the gap</span>` : "";
    return `<tr>
      <td>${esc(SEGMENT_LABELS[seg])}</td>
      <td class="num tnum">${esc(duration(officialTime))}${officialMark}</td>
      <td class="num tnum">${esc(duration(recordedTime))}${recordedMark}</td>
      <td class="num tnum">${esc(delta(d))}</td>
      <td class="num tnum">${esc(distance(rec?.distance_m ?? null))}</td>
      <td class="num tnum">${esc(pace(seg, rec?.distance_m ?? null, recordedTime))}</td>
    </tr>`;
  }

  function totalRow() {
    const recordedComplete = SEGMENTS.every((s) => bySegment[s]?.elapsed_time != null);
    const recordedTotal = recordedComplete
      ? SEGMENTS.reduce((sum, s) => sum + /** @type {number} */ (bySegment[s].elapsed_time), 0)
      : null;
    const officialTotal = official?.total_time ?? null;
    const d = officialTotal != null && recordedTotal != null ? recordedTotal - officialTotal : null;
    const officialMark = official?.total_is_derived ? ` <span class="marker">derived</span>` : "";
    return `<tr class="total-row">
      <td>Total</td>
      <td class="num tnum">${esc(duration(officialTotal))}${officialMark}</td>
      <td class="num tnum">${esc(duration(recordedTotal))}</td>
      <td class="num tnum">${esc(delta(d))}</td>
      <td class="num">${DASH}</td>
      <td class="num">${DASH}</td>
    </tr>`;
  }
}

async function loadTrack() {
  const body = /** @type {HTMLElement} */ (document.getElementById("map-body"));
  /** @type {import("./types.js").TrackSegment[]} */
  let track = [];
  try {
    track = await api(`/races/${id}/track`);
  } catch (err) {
    body.innerHTML = `<p class="muted">No recorded track</p>`;
    return;
  }
  const withBounds = track.filter((t) => t.bounds && t.points.length);
  if (!withBounds.length) {
    body.innerHTML = `<p class="muted">No recorded track</p>`;
    return;
  }

  body.innerHTML = `<div id="leaflet-map" class="map"></div>`;
  const L = /** @type {any} */ (window).L;
  const map = L.map("leaflet-map", { scrollWheelZoom: false });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(map);

  /** @type {any[]} */
  const allBounds = [];
  for (const seg of withBounds) {
    const colour = seg.segment === "t1" || seg.segment === "t2"
      ? token("--transition") : token(`--${seg.segment}`);
    const latlngs = seg.points.map((p) => [p.lat, p.lng]);
    L.polyline(latlngs, { color: colour, weight: 3 }).addTo(map);
    const [n, s, e, w] = /** @type {number[]} */ (seg.bounds);
    allBounds.push([n, e], [s, w]);
  }
  map.fitBounds(allBounds, { padding: [16, 16] });
}

async function loadLaps() {
  const body = /** @type {HTMLElement} */ (document.getElementById("pacing-body"));
  try {
    allLaps = await api(`/races/${id}/laps`);
  } catch (err) {
    body.innerHTML = `<p class="error">Could not load laps.</p>`;
    return;
  }
  const sports = SPORTS.filter((sp) => allLaps.some((l) => l.segment === sp));
  if (!sports.length) {
    body.innerHTML = `<p class="muted">No lap data.</p>`;
    return;
  }
  activeSport = sports[0];
  body.innerHTML = `
    <div class="tabs" role="tablist">
      ${sports.map((sp) => `<button type="button" role="tab" data-sport="${sp}"
        aria-selected="${sp === activeSport}">${esc(SEGMENT_LABELS[sp])}</button>`).join("")}
    </div>
    <div id="pacing-panel"></div>
  `;
  body.querySelectorAll("[data-sport]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeSport = /** @type {HTMLElement} */ (btn).dataset.sport ?? null;
      body.querySelectorAll("[data-sport]").forEach((b) =>
        b.setAttribute("aria-selected", String(b === btn)));
      renderPacingPanel();
    });
  });
  renderPacingPanel();
}

function renderPacingPanel() {
  const panel = /** @type {HTMLElement} */ (document.getElementById("pacing-panel"));
  const sport = /** @type {string} */ (activeSport);
  const laps = allLaps.filter((l) => l.segment === sport)
    .sort((a, b) => a.lap_index - b.lap_index);
  if (paceChart) { paceChart.destroy(); paceChart = null; }

  if (laps.length <= 1) {
    const bySegment = /** @type {Record<string, import("./types.js").SegmentOut>} */ ({});
    for (const s of /** @type {import("./types.js").RaceDetail} */ (race).segments) bySegment[s.segment] = s;
    const seg = bySegment[sport];
    const lap = laps[0];
    const d = seg?.distance_m ?? lap?.distance_m ?? null;
    const t = seg?.elapsed_time ?? lap?.total_time ?? null;
    const hr = seg?.avg_hr ?? lap?.avg_hr ?? null;
    const pw = seg?.avg_power ?? lap?.avg_power ?? null;
    panel.innerHTML = `
      <div class="summary-tile">
        <div><span class="muted small">Distance</span><br><span class="tnum">${esc(distance(d))}</span></div>
        <div><span class="muted small">Time</span><br><span class="tnum">${esc(duration(t))}</span></div>
        <div><span class="muted small">Pace</span><br><span class="tnum">${esc(pace(sport, d, t))}</span></div>
        <div><span class="muted small">HR</span><br><span class="tnum">${esc(hr ?? DASH)}</span></div>
        <div><span class="muted small">Power</span><br><span class="tnum">${esc(pw ?? DASH)}</span></div>
      </div>
    `;
    return;
  }

  const labels = laps.map((l) => `Lap ${l.lap_index + 1}${l.is_partial ? " (partial)" : ""}`);
  const paceValues = laps.map((l) => paceValue(sport, l.distance_m, l.total_time));
  const hrValues = laps.map((l) => l.avg_hr ?? null);
  const powerValues = laps.map((l) => l.avg_power ?? null);

  panel.innerHTML = `<div class="chart"><canvas id="pacing-canvas"></canvas></div>`;
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("pacing-canvas"));
  const paceFormat = sport === "bike" ? (/** @type {number} */ v) => `${v.toFixed(1)} km/h`
    : sport === "swim" ? (/** @type {number} */ v) => `${duration(v)} /100 m`
    : (/** @type {number} */ v) => `${duration(v)} /km`;
  /** @type {any[]} */
  const series = [
    {
      label: sport === "bike" ? "Speed" : "Pace", values: paceValues, colour: token(`--${sport}`),
      axis: "pace", reverse: sport !== "bike", format: paceFormat,
    },
    { label: "HR", values: hrValues, colour: token("--slower"), axis: "hr" },
    { label: "Power", values: powerValues, colour: token("--neutral"), axis: "power" },
  ];
  paceChart = lineChart(canvas, { labels, series });

  const cols = ["Lap", "Distance", "Time", "Pace", "HR", "Power"];
  const rows = laps.map((l) => [
    `${l.lap_index + 1}${l.is_partial ? " (partial)" : ""}`,
    distance(l.distance_m), duration(l.total_time), pace(sport, l.distance_m, l.total_time),
    l.avg_hr ?? DASH, l.avg_power ?? DASH,
  ]);
  panel.insertAdjacentHTML("beforeend", tableFallback(`${SEGMENT_LABELS[sport]} laps`, cols, rows));
}

function renderConditions() {
  const r = /** @type {import("./types.js").RaceDetail} */ (race);
  const bySegment = /** @type {Record<string, import("./types.js").SegmentOut>} */ ({});
  for (const s of r.segments) bySegment[s.segment] = s;
  const official = r.official_result;

  const w = r.weather;
  const weatherLine = [
    w.temp_c != null ? `${Math.round(w.temp_c)} °C` : null,
    w.humidity_pct != null ? `${Math.round(w.humidity_pct)} %` : null,
    w.conditions ?? null,
  ].filter(Boolean);

  const fuelRows = r.fueling.map((f) => {
    const segTime = bySegment[f.segment]?.elapsed_time ?? official?.[`${f.segment}_time`] ?? null;
    const gph = f.carbs_g != null && segTime ? (f.carbs_g / (segTime / 3600)) : null;
    const products = f.products.length
      ? f.products.map((p) => p.quantity != null ? `${esc(p.name)} &times;${esc(p.quantity)}` : esc(p.name)).join(", ")
      : DASH;
    const gearNames = bySegment[f.segment]?.gear?.map((g) => g.name).join(", ") || DASH;
    return `<tr>
      <td>${esc(SEGMENT_LABELS[f.segment] ?? f.segment)}</td>
      <td class="num tnum">${esc(number(f.carbs_g, "g"))}</td>
      <td class="num tnum">${esc(number(gph, "g/h"))}</td>
      <td>${products}</td>
      <td>${f.notes ? esc(f.notes) : DASH}</td>
      <td>${esc(gearNames)}</td>
    </tr>`;
  }).join("");

  document.getElementById("race-conditions").innerHTML = `
    <h2>Conditions &amp; fueling</h2>
    <p class="muted">${weatherLine.length ? esc(weatherLine.join(" · ")) : DASH}</p>
    ${r.fueling.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Segment</th><th class="num">Carbs</th><th class="num">g/h</th>
        <th>Products</th><th>Notes</th><th>Gear</th></tr></thead>
      <tbody>${fuelRows}</tbody>
    </table></div>` : `<p class="muted">No fueling recorded.</p>`}
  `;
}

function renderNotes() {
  const r = /** @type {import("./types.js").RaceDetail} */ (race);
  document.getElementById("race-notes").innerHTML = `
    <h2>Notes</h2>
    <p class="notes-text">${r.notes ? esc(r.notes) : DASH}</p>
  `;
}

function renderRecordings() {
  const section = /** @type {HTMLElement} */ (document.getElementById("race-recordings"));
  if (!isOwner || !race?.recordings) { section.innerHTML = ""; return; }
  const bySegment = /** @type {Record<string, import("./types.js").Recording[]>} */ ({});
  for (const rec of race.recordings) (bySegment[rec.segment] ??= []).push(rec);
  const groups = Object.entries(bySegment).filter(([, list]) => list.length > 1);
  if (!groups.length) { section.innerHTML = ""; return; }

  section.innerHTML = `
    <h2>Recordings</h2>
    ${groups.map(([seg, list]) => `
      <h3>${esc(SEGMENT_LABELS[seg] ?? seg)}</h3>
      <ul class="recordings-list">
        ${list.map((rec) => `<li>
          <span>${esc(rec.device_key ?? `Recording ${rec.id}`)}</span>
          ${rec.is_primary ? `<span class="badge">primary</span>`
            : `<button type="button" class="button" data-activity-id="${rec.id}">Make primary</button>`}
        </li>`).join("")}
      </ul>
    `).join("")}
  `;
  section.querySelectorAll("[data-activity-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const activityId = /** @type {HTMLElement} */ (btn).dataset.activityId;
      try {
        race = await api(`/activities/${activityId}`, { method: "PATCH", body: { is_primary: true } });
        renderHeader();
        renderSplits();
        renderConditions();
        renderRecordings();
        loadTrack();
        loadLaps();
      } catch (err) {
        fail(section, err);
      }
    });
  });
}

main_().catch((err) => fail(main, err));
