// @ts-check
// Pieces every page shares: the header, the owner bar, the segment bar, table fallbacks.

import { api, me } from "./api.js";
import { DASH, SEGMENTS, SEGMENT_LABELS, duration, esc } from "./format.js";

/**
 * Render the navbar at the top of <body>: the home mark (always the timeline at "/"),
 * the page links, and, for the owner, the owner tools and log out.
 * @param {{edit?: string}} [links]  an extra "Edit" link for the current page
 * @returns {Promise<{owner: boolean, site_mode: string, site: import("./types.js").Site}>}
 */
export async function header(links = {}) {
  const [who, site] = await Promise.all([me(), api("/site")]);
  const link = (/** @type {string} */ href, /** @type {string} */ label) =>
    `<a href="${esc(href)}"${location.pathname === href ? ' aria-current="page"' : ""}>${esc(label)}</a>`;
  const owner = who.owner
    ? `<span class="divider" role="separator"></span>
      ${links.edit ? link(links.edit, "Edit") : ""}
      ${link("/admin/import", "Import")}${link("/admin/gear", "Gear")}${link("/admin/site", "Site")}`
    : "";
  const nav = document.createElement("header");
  nav.className = "navbar";
  nav.innerHTML = `<div class="navbar-inner">
    <a class="home" href="/"${location.pathname === "/" ? ' aria-current="page"' : ""}
       aria-label="${esc(site.site_title)}, home timeline">
      <span class="home-mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="home-text">
        <span class="home-title">${esc(site.site_title)}</span>
        ${site.athlete_name ? `<span class="home-sub">${esc(site.athlete_name)}</span>` : ""}
      </span>
    </a>
    <nav class="navbar-links" aria-label="Site">${link("/trends", "Trends")}${owner}</nav>
    ${who.owner ? `<div class="navbar-end">
      <span class="muted">${who.site_mode === "public" ? "Public site" : "Private site"}</span>
      <button class="link" type="button" data-logout>Log out</button>
    </div>` : ""}
  </div>`;
  nav.querySelector("[data-logout]")?.addEventListener("click", async () => {
    await api("/auth/logout", { method: "POST" });
    location.href = "/";
  });
  document.body.prepend(nav);
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
