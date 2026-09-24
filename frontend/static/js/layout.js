// @ts-check
// Pieces every page shares: the header, the owner bar, the segment bar, table fallbacks.

import { api, me } from "./api.js";
import { DASH, SEGMENTS, SEGMENT_LABELS, duration, esc } from "./format.js";

/**
 * Render the site header (and, for the owner, the owner bar) at the top of <body>.
 * @param {{edit?: string}} [links]  an extra "Edit" link for the current page
 * @returns {Promise<{owner: boolean, site_mode: string, site: import("./types.js").Site}>}
 */
export async function header(links = {}) {
  const [who, site] = await Promise.all([me(), api("/site")]);
  const head = document.createElement("header");
  head.className = "page site-header";
  head.style.paddingBottom = "0";
  head.innerHTML = `<a class="site-title" href="/">${esc(site.site_title)}</a>
    ${site.athlete_name ? `<span class="muted">${esc(site.athlete_name)}</span>` : ""}
    <nav class="site-nav" aria-label="Site"><a href="/">Races</a><a href="/trends">Trends</a></nav>`;
  head.querySelectorAll(".site-nav a").forEach((a) => {
    if (a.getAttribute("href") === location.pathname) a.setAttribute("aria-current", "page");
  });
  document.body.prepend(head);
  if (who.owner) {
    const bar = document.createElement("nav");
    bar.className = "owner-bar";
    bar.setAttribute("aria-label", "Owner");
    bar.innerHTML = `
      ${links.edit ? `<a href="${esc(links.edit)}">Edit</a>` : ""}
      <a href="/admin/import">Import</a>
      <a href="/admin/gear">Gear</a>
      <a href="/admin/site">Site</a>
      <span class="spacer"></span>
      <span class="muted">${who.site_mode === "public" ? "Public site" : "Private site"}</span>
      <button class="link" type="button" data-logout>Log out</button>`;
    bar.querySelector("[data-logout]")?.addEventListener("click", async () => {
      await api("/auth/logout", { method: "POST" });
      location.href = "/";
    });
    document.body.prepend(bar);
  }
  return { ...who, site };
}

/**
 * The swim/bike/run proportion bar: widths proportional to time; transitions are
 * neutral slivers, derived ones hatched. Unknown segments are left out.
 * @param {Record<string, number|null>} times
 * @param {string[]} [derived]
 */
export function segmentBar(times, derived = []) {
  const known = SEGMENTS.filter((s) => times[s]);
  const total = known.reduce((sum, s) => sum + /** @type {number} */ (times[s]), 0);
  if (!total) return "";
  const title = known.map((s) => `${SEGMENT_LABELS[s]} ${duration(times[s])}`).join(", ");
  const parts = known.map((s) => {
    const cls = `${s}${derived.includes(s) ? " derived" : ""}`;
    return `<span class="${cls}" style="flex: ${times[s]} 0 0"></span>`;
  });
  return `<div class="segment-bar" role="img" aria-label="${esc(title)}">${parts.join("")}</div>`;
}

/**
 * A <details> table with the same numbers as a chart (required: a canvas is opaque).
 * @param {string} summary
 * @param {string[]} columns
 * @param {(string|number|null)[][]} rows  already formatted
 */
export function tableFallback(summary, columns, rows) {
  return `<details><summary>${esc(summary)}</summary><div class="table-wrap"><table>
    <thead><tr>${columns.map((c, i) => `<th class="${i ? "num" : ""}">${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((v, i) =>
      `<td class="${i ? "num tnum" : ""}">${esc(v ?? DASH)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div></details>`;
}

/** Show an error in place of page content. */
export function fail(/** @type {Element} */ where, /** @type {unknown} */ error) {
  const message = error instanceof Error ? error.message : String(error);
  where.innerHTML = `<p class="error">${esc(message)}</p>`;
}
