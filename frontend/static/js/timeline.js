// @ts-check
// The home page: every race in date order, newest first (docs/features/timeline.md).

import { api } from "./api.js";
import { header, segmentBar, fail } from "./layout.js";
import { date, duration, distance, elevation, esc } from "./format.js";

/** @typedef {import("./types.js").RaceListItem} RaceListItem */

const PAGE_SIZE = 20;

async function main() {
  const main = /** @type {HTMLElement} */ (document.getElementById("main"));
  let who;
  try {
    who = await header();
  } catch (err) {
    fail(main, err);
    return;
  }

  main.innerHTML = `
    <p class="muted" id="about"></p>
    <p class="stat-strip muted small" id="stats"></p>
    <div id="list"></div>
    <p id="more-wrap" class="hidden"><button type="button" id="more">Show more</button></p>
  `;
  const aboutEl = /** @type {HTMLElement} */ (document.getElementById("about"));
  if (who.site.about) {
    aboutEl.textContent = who.site.about;
  } else {
    aboutEl.remove();
  }

  try {
    const stats = await api("/stats/summary");
    document.getElementById("stats").innerHTML = statStrip(stats);
  } catch (err) {
    document.getElementById("stats")?.remove();
  }

  const listEl = /** @type {HTMLElement} */ (document.getElementById("list"));
  const moreWrap = /** @type {HTMLElement} */ (document.getElementById("more-wrap"));
  const moreBtn = /** @type {HTMLButtonElement} */ (document.getElementById("more"));

  let offset = 0;
  let total = 0;
  /** @type {RaceListItem[]} */
  let shown = [];

  async function loadMore() {
    moreBtn.disabled = true;
    try {
      /** @type {{items: RaceListItem[], total: number}} */
      const page = await api(`/races?limit=${PAGE_SIZE}&offset=${offset}`);
      total = page.total;
      shown = shown.concat(page.items);
      offset += page.items.length;
      render();
      moreWrap.classList.toggle("hidden", offset >= total);
    } catch (err) {
      fail(listEl, err);
    } finally {
      moreBtn.disabled = false;
    }
  }

  function render() {
    if (!shown.length) {
      listEl.innerHTML = emptyState(who.owner, who.site.site_title);
      return;
    }
    listEl.innerHTML = `<div class="timeline">${shown.map((r) => card(r, who)).join("")}</div>`;
  }

  moreBtn.addEventListener("click", loadMore);
  await loadMore();
}

/** @param {{race_count: number, distance_m: number, distance_race_count: number,
 *   elevation_gain_m: number, elevation_race_count: number}} stats */
function statStrip(stats) {
  const parts = [`${stats.race_count} race${stats.race_count === 1 ? "" : "s"}`];
  if (stats.distance_race_count > 0) {
    const denom = stats.distance_race_count < stats.race_count
      ? ` across ${stats.distance_race_count} of ${stats.race_count} races` : "";
    parts.push(`${distance(stats.distance_m)}${denom}`);
  }
  if (stats.elevation_race_count > 0) {
    const denom = stats.elevation_race_count < stats.race_count
      ? ` across ${stats.elevation_race_count} of ${stats.race_count} races` : "";
    parts.push(`${elevation(stats.elevation_gain_m)} climbed${denom}`);
  }
  return esc(parts.join(" · "));
}

/** @param {boolean} owner @param {string} siteTitle */
function emptyState(owner, siteTitle) {
  if (owner) {
    return `<p class="muted">No races yet. <a href="/admin/import">Import your first race</a>.</p>`;
  }
  return `<p class="muted">${esc(siteTitle)}<br>Nothing here yet.</p>`;
}

/** @param {RaceListItem} race @param {{owner: boolean, site_mode: string}} who */
function card(race, who) {
  const notPublic = who.owner && who.site_mode === "public" && !race.is_public
    ? `<span class="marker">not public</span>` : "";
  const sourceNote = race.times_source === "official" ? "official"
    : race.times_source === "recorded" ? "recorded" : "";
  return `
    <div class="timeline-row">
      <span class="dot-marker"></span>
      <a class="card race-card${race.cover_photo ? " has-photo" : ""}" href="/races/${race.id}">
        ${race.cover_photo ? `<img class="race-card-photo" src="${esc(race.cover_photo.thumb_url)}"
          loading="lazy" alt="" width="${race.cover_photo.width}" height="${race.cover_photo.height}">` : ""}
        <div class="race-card-head">
          <span class="race-name">${esc(race.name)}</span>
          ${race.category ? `<span class="badge">${esc(race.category.label)}</span>` : ""}
          ${notPublic}
        </div>
        <div class="muted small">${esc(date(race.date))}</div>
        <div class="race-card-time tnum">${esc(duration(race.total_time))}
          ${sourceNote ? `<span class="muted small">${sourceNote}</span>` : ""}</div>
        ${segmentBar(race.segment_times, race.derived_segments)}
      </a>
    </div>`;
}

main();
