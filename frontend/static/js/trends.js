// @ts-check
// Cross-race trends: /trends. docs/features/trends.md.

import { api } from "./api.js";
import { header, tableFallback, fail } from "./layout.js";
import {
  DASH, SPORTS, SEGMENT_LABELS, duration, number, pace, paceValue, date as fmtDate, esc,
} from "./format.js";
import { xyChart, token } from "./charts.js";

/** @typedef {import("./types.js").TrendRace} TrendRace */

const main = /** @type {HTMLElement} */ (document.getElementById("main"));
const DAY_MS = 86_400_000;
const MIN_POINTS = 2; // one race is a point, not a trend

/** @type {TrendRace[]} */
let allRaces = [];
/** @type {any[]} */
let charts = [];
let weatherSport = "run";

/** "2026-06-21" → days since the epoch; a linear axis keeps uneven gaps honest. */
const dayNumber = (/** @type {string} */ iso) => {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / DAY_MS;
};
const dayLabel = (/** @type {number} */ day) =>
  fmtDate(new Date(Math.round(day) * DAY_MS).toISOString().slice(0, 10));

/** Pace as a chart value and its label: swim /100 m and run /km (lower is faster), bike km/h. */
function paceFormat(/** @type {string} */ sport) {
  if (sport === "bike") return (/** @type {number} */ v) => `${v.toFixed(1)} km/h`;
  const unit = sport === "swim" ? "/100 m" : "/km";
  return (/** @type {number} */ v) => `${duration(v)} ${unit}`;
}

/** @param {TrendRace} race @param {string} sport */
function paceOf(race, sport) {
  const s = race.sports[sport];
  return s ? paceValue(sport, s.distance_m, s.elapsed_time) : null;
}

async function run() {
  await header();
  try {
    allRaces = (await api("/trends")).races;
  } catch (err) {
    fail(main, err);
    return;
  }

  const categories = new Map();
  for (const r of allRaces) if (r.category) categories.set(r.category.key, r.category.label);

  main.innerHTML = `
    <h1>Trends</h1>
    <p class="muted small">Pace from the recorded distance and time of each race. Courses
      differ, so compare like with like: filter by category.</p>
    <div class="trends-filter">
      <label for="category">Category</label>
      <select id="category">
        <option value="">All races</option>
        ${[...categories].map(([key, label]) =>
          `<option value="${esc(key)}">${esc(label)}</option>`).join("")}
      </select>
    </div>
    <section id="pace-section"><h2>Pace over time</h2><div id="pace-body"></div></section>
    <section id="weather-section"><h2>Temperature and pace</h2>
      <div class="tabs" role="tablist">
        ${SPORTS.map((sp) => `<button type="button" role="tab" data-sport="${sp}"
          aria-selected="${sp === weatherSport}">${esc(SEGMENT_LABELS[sp])}</button>`).join("")}
      </div>
      <div id="weather-body"></div>
    </section>
    <section id="table-section"></section>
  `;
  const select = /** @type {HTMLSelectElement} */ (document.getElementById("category"));
  select.addEventListener("change", render);
  main.querySelectorAll("[data-sport]").forEach((btn) => btn.addEventListener("click", () => {
    weatherSport = /** @type {HTMLElement} */ (btn).dataset.sport ?? "run";
    main.querySelectorAll("[data-sport]").forEach((b) =>
      b.setAttribute("aria-selected", String(b === btn)));
    render();
  }));
  render();
}

function selected() {
  const key = /** @type {HTMLSelectElement} */ (document.getElementById("category")).value;
  return key ? allRaces.filter((r) => r.category?.key === key) : allRaces;
}

function render() {
  charts.forEach((c) => c.destroy());
  charts = [];
  const races = selected();
  renderPace(races);
  renderWeather(races);
  renderTable(races);
}

/** @param {TrendRace[]} races */
function renderPace(races) {
  const body = /** @type {HTMLElement} */ (document.getElementById("pace-body"));
  const blocks = SPORTS.map((sport) => {
    const points = races.filter((r) => paceOf(r, sport) !== null);
    return { sport, points };
  });
  if (blocks.every((b) => b.points.length < MIN_POINTS)) {
    body.innerHTML = `<p class="muted">Pace trends need at least ${MIN_POINTS} races with a
      recorded distance for the same sport.</p>`;
    return;
  }
  body.innerHTML = blocks.map(({ sport, points }) => `
    <div class="trend-chart">
      <h3><span class="dot ${sport}"></span>${esc(SEGMENT_LABELS[sport])}</h3>
      ${points.length < MIN_POINTS
        ? `<p class="muted small">${points.length ? "Only one race" : "No races"} with a
            recorded ${esc(SEGMENT_LABELS[sport].toLowerCase())} distance.</p>`
        : `<div class="chart"><canvas id="pace-${sport}"></canvas></div>`}
    </div>`).join("");
  for (const { sport, points } of blocks) {
    if (points.length < MIN_POINTS) continue;
    const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById(`pace-${sport}`));
    charts.push(xyChart(canvas, {
      points: points.map((r) => ({
        x: dayNumber(r.date), y: /** @type {number} */ (paceOf(r, sport)), label: r.name,
      })),
      colour: token(`--${sport}`),
      line: true,
      xTitle: "race date",
      yTitle: sport === "bike" ? "speed" : "pace",
      reverse: sport !== "bike", // faster is always up
      xFormat: dayLabel,
      yFormat: paceFormat(sport),
    }));
  }
}

/** @param {TrendRace[]} races */
function renderWeather(races) {
  const body = /** @type {HTMLElement} */ (document.getElementById("weather-body"));
  const sport = weatherSport;
  const points = races.filter((r) => r.temp_c !== null && paceOf(r, sport) !== null);
  const without = races.filter((r) => paceOf(r, sport) !== null && r.temp_c === null).length;
  const note = without
    ? `<p class="muted small">${without} race${without === 1 ? "" : "s"} without weather left out.</p>`
    : "";
  if (points.length < MIN_POINTS) {
    body.innerHTML = `<p class="muted">Needs at least ${MIN_POINTS} races with both weather and
      a recorded ${esc(SEGMENT_LABELS[sport].toLowerCase())} distance.</p>${note}`;
    return;
  }
  body.innerHTML = `<div class="chart"><canvas id="weather-canvas"></canvas></div>${note}`;
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("weather-canvas"));
  charts.push(xyChart(canvas, {
    points: points.map((r) => ({
      x: /** @type {number} */ (r.temp_c), y: /** @type {number} */ (paceOf(r, sport)),
      label: `${r.name}, ${fmtDate(r.date)}`,
    })),
    colour: token(`--${sport}`),
    xTitle: "temperature at the start (°C)",
    yTitle: sport === "bike" ? "speed" : "pace",
    reverse: sport !== "bike",
    xFormat: (v) => `${Math.round(v)} °C`,
    yFormat: paceFormat(sport),
  }));
}

/** The table fallback for every chart on the page: the same numbers, newest first. */
function renderTable(/** @type {TrendRace[]} */ races) {
  const section = /** @type {HTMLElement} */ (document.getElementById("table-section"));
  if (!races.length) {
    section.innerHTML = `<p class="muted">No races yet.</p>`;
    return;
  }
  const cols = ["Race", "Date", "Category", "Temp", "Swim", "Bike", "Run"];
  const rows = [...races].reverse().map((r) => [
    r.name, fmtDate(r.date), r.category?.label ?? DASH, number(r.temp_c, "°C"),
    ...SPORTS.map((sp) => {
      const s = r.sports[sp];
      return s ? pace(sp, s.distance_m, s.elapsed_time) : DASH;
    }),
  ]);
  section.innerHTML = tableFallback("All races as a table", cols, rows);
}

run().catch((err) => fail(main, err));
