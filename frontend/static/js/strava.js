// @ts-check
// Strava pieces several pages share: the brand button and link, the messages for the
// `?strava=` the OAuth flow lands back with, and the "Pull new races from Strava" panel
// (docs/features/strava.md). Strava's brand guidelines fix the button and link wording.

import { api, ApiError } from "./api.js";
import { esc } from "./format.js";

/** @typedef {import("./types.js").PullOut} PullOut */

/** What `?strava=` means after the browser comes back from Strava. */
export const STRAVA_MESSAGES = {
  connected: "Strava is connected.",
  other_athlete: "That's a different Strava account from the one linked to this site. "
    + "Log in to Strava as the linked athlete and try again.",
  not_owner: "That Strava account isn't this site's owner.",
  denied: "Strava wasn't given access, so nothing changed.",
  expired: "The Strava sign-in took too long or was started in another tab. Try again.",
  failed: "Strava couldn't finish the sign-in. Try again in a moment.",
};

/**
 * Read `?strava=` once and take it out of the address bar, so a reload doesn't repeat it.
 * @returns {{code: string, message: string, ok: boolean} | null}
 */
export function takeStravaParam() {
  const params = new URLSearchParams(location.search);
  const code = params.get("strava");
  if (!code) return null;
  params.delete("strava");
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  const message = STRAVA_MESSAGES[/** @type {keyof typeof STRAVA_MESSAGES} */ (code)];
  return message ? { code, message, ok: code === "connected" } : null;
}

/**
 * The orange Strava button: a link, since it starts a full-page OAuth navigation.
 * @param {"Connect with Strava"|"Log in with Strava"} label
 * @param {string} next  a same-site path to come back to
 */
export function connectButton(label, next) {
  return `<a class="strava-button" href="/auth/strava?next=${esc(encodeURIComponent(next))}">${esc(label)}</a>`;
}

/**
 * "View on Strava", required next to anything shown from Strava. Empty without a URL.
 * @param {string|null|undefined} url
 */
export function viewOnStrava(url) {
  if (!url || !url.startsWith("https://")) return "";
  return `<a class="strava-link" href="${esc(url)}" target="_blank" rel="noopener">View on Strava</a>`;
}

/**
 * The "Pull new races from Strava" button, its "search everything" option, and the
 * summary of what the pull did.
 * @param {HTMLElement} container
 * @param {{compact?: boolean, onDone?: (out: PullOut) => void}} [options]
 */
export function pullPanel(container, { compact = false, onDone } = {}) {
  container.innerHTML = `<div class="strava-pull${compact ? " compact" : ""}">
    <div class="strava-pull-controls">
      <button type="button" class="strava-pull-button">Pull new races from Strava</button>
      <label class="strava-check"><input type="checkbox" class="strava-everything">
        Search all of Strava, not just recent activities</label>
    </div>
    <p class="status" role="status" aria-live="polite"></p>
    <div class="strava-pull-result"></div>
  </div>`;
  const button = /** @type {HTMLButtonElement} */ (container.querySelector(".strava-pull-button"));
  const everything = /** @type {HTMLInputElement} */ (container.querySelector(".strava-everything"));
  const status = /** @type {HTMLElement} */ (container.querySelector(".status"));
  const result = /** @type {HTMLElement} */ (container.querySelector(".strava-pull-result"));

  button.addEventListener("click", async () => {
    button.disabled = true;
    everything.disabled = true;
    status.textContent = "Pulling from Strava…";
    status.classList.remove("error");
    result.innerHTML = "";
    try {
      /** @type {PullOut} */
      const out = await api("/strava/pull", { method: "POST", body: { everything: everything.checked } });
      status.textContent = "";
      result.innerHTML = pullSummary(out);
      onDone?.(out);
    } catch (err) {
      status.textContent = err instanceof ApiError ? err.message : "The pull didn't finish.";
      status.classList.add("error");
      if (err instanceof ApiError && err.status === 409) {
        result.innerHTML = `<p>${connectButton("Connect with Strava", location.pathname)}</p>`;
      }
    } finally {
      button.disabled = false;
      everything.disabled = false;
    }
  });
}

/** @param {PullOut} out */
function pullSummary(out) {
  const races = out.races.map((r) => `<li class="result-line">
      <a href="/admin/race-edit?id=${esc(r.race_id)}">${esc(r.name)}</a>
      <span class="muted">${r.created ? "new race" : "added to this race"}</span>
      ${r.outcomes.length ? `<ul class="strava-outcomes">${r.outcomes.map((o) =>
        `<li class="result-line ${esc(o.result)}">${esc(o.filename ?? "Activity")}: ${esc(o.message)}</li>`,
      ).join("")}</ul>` : ""}
    </li>`).join("");
  const skipped = out.skipped.map((s) => `<li class="result-line">
      ${esc(s.name ?? `Activity ${s.activity_id}`)}: <span class="muted">${esc(s.message)}</span></li>`).join("");
  const errors = out.tagline_errors.map((e) => `<li class="result-line failed">${esc(e)}</li>`).join("");
  return `
    <p class="${out.complete ? "" : "strava-incomplete"}">${esc(out.message)}</p>
    ${races ? `<ul class="result-list">${races}</ul>` : ""}
    ${skipped ? `<details class="strava-skipped"><summary>${out.skipped.length}
      ${out.skipped.length === 1 ? "activity" : "activities"} skipped</summary>
      <ul class="result-list">${skipped}</ul></details>` : ""}
    ${out.taglines ? `<p class="small muted tnum">${out.taglines}
      tagline${out.taglines === 1 ? "" : "s"} written to Strava</p>` : ""}
    ${errors ? `<p class="small">Some taglines couldn't be written:</p>
      <ul class="result-list">${errors}</ul>` : ""}
    <p class="small muted tnum">${out.scanned} activit${out.scanned === 1 ? "y" : "ies"} checked.</p>`;
}
