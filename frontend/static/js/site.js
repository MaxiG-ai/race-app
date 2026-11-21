// @ts-check
// Owner-only site settings: site_title, athlete_name, about. Autosave on blur, one
// changed field per request (docs/features/access-and-visibility.md). Below them, the
// Strava connection (docs/features/strava.md).

import { api, ApiError } from "./api.js";
import { header, fail } from "./layout.js";
import { esc, dateTime, DASH } from "./format.js";
import { connectButton, viewOnStrava, pullPanel, takeStravaParam } from "./strava.js";

/** @typedef {import("./types.js").Site} Site */
/** @typedef {import("./types.js").StravaStatus} StravaStatus */

const HERE = "/admin/site";

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
    main.innerHTML = `<p class="error">Owner only.</p>`;
    return;
  }

  const site = who.site;
  main.innerHTML = `
    <h1>Site</h1>
    ${field("site_title", "Site title", "input", site.site_title ?? "")}
    ${field("athlete_name", "Athlete name", "input", site.athlete_name ?? "")}
    ${field("about", "About", "textarea", site.about ?? "")}
    <section class="strava-section" aria-labelledby="strava-heading">
      <h2 id="strava-heading">Strava</h2>
      <p class="strava-flash" id="strava-flash" role="status"></p>
      <div id="strava-body"><p class="muted">Loading…</p></div>
    </section>
  `;

  for (const key of /** @type {const} */ (["site_title", "athlete_name", "about"])) {
    const el = /** @type {HTMLInputElement|HTMLTextAreaElement} */
      (document.getElementById(key));
    const status = /** @type {HTMLElement} */ (document.getElementById(`${key}-status`));
    let saved = el.value;
    el.addEventListener("blur", async () => {
      if (el.value === saved) return;
      status.textContent = "Saving…";
      status.classList.remove("error");
      const value = el.value.trim() === "" ? null : el.value;
      try {
        /** @type {Site} */
        const updated = await api("/site", { method: "PATCH", body: { [key]: value } });
        saved = el.value = updated[key] ?? "";
        status.textContent = "Saved";
      } catch (err) {
        status.textContent = err instanceof ApiError ? err.message : "Couldn't save.";
        status.classList.add("error");
        el.value = saved; // the server rejected it; the stored value is unchanged
      }
    });
  }

  const returned = takeStravaParam();
  if (returned) flash(returned.message, !returned.ok);
  loadStrava();
}

// --- Strava ---------------------------------------------------------------------------------

/** @param {string} text @param {boolean} [isError] */
function flash(text, isError = false) {
  const el = /** @type {HTMLElement} */ (document.getElementById("strava-flash"));
  el.textContent = text;
  el.classList.toggle("error", isError);
}

async function loadStrava() {
  const body = /** @type {HTMLElement} */ (document.getElementById("strava-body"));
  try {
    renderStrava(await api("/strava"));
  } catch (err) {
    fail(body, err);
  }
}

/** @param {StravaStatus} s */
function renderStrava(s) {
  const body = /** @type {HTMLElement} */ (document.getElementById("strava-body"));
  if (!s.enabled) {
    body.innerHTML = `<p class="small muted">Strava isn't set up on this server. It needs
      <code>STRAVA_CLIENT_ID</code> and <code>STRAVA_CLIENT_SECRET</code> in the server's
      environment (see <code>docs/features/strava.md</code>).</p>`;
    return;
  }
  if (!s.connected) {
    body.innerHTML = `<p class="small">Connect your Strava account to pull your races from it.
      Once it's connected, you log in with Strava instead of a password.</p>
      <p>${connectButton("Connect with Strava", HERE)}</p>`;
    return;
  }

  const name = s.athlete_name ?? (s.athlete_id ? `athlete ${s.athlete_id}` : "your athlete");
  const profile = s.athlete_id
    ? viewOnStrava(`https://www.strava.com/athletes/${encodeURIComponent(s.athlete_id)}`) : "";
  const missing = [
    s.can_pull ? "" : `<p>Strava didn't grant access to your activities, so races can't be
      pulled. Reconnect and allow it.</p>`,
    s.can_write ? "" : `<p>Strava didn't grant permission to edit your activities, so taglines
      can't be written. Reconnect and allow it.</p>`,
  ].join("");

  body.innerHTML = `
    <p class="strava-line">Connected as <strong>${esc(name)}</strong> ${profile}</p>
    ${missing ? `<div class="strava-line strava-warning">${missing}
      <p>${connectButton("Connect with Strava", HERE)}</p></div>` : ""}
    <p class="strava-line">Last pull: <span class="tnum" id="strava-last-pull"></span></p>
    <p class="strava-line small muted tnum" id="strava-rate"></p>
    ${s.can_pull ? `<div class="strava-block" id="strava-pull"></div>` : ""}

    <div class="strava-block">
      <label class="strava-check"><input type="checkbox" id="strava-tagline"
        ${s.tagline ? "checked" : ""} ${s.can_write ? "" : "disabled"}>
        Write a tagline on pulled races</label>
      <p class="small muted">Each pull adds one line to the description of the race's activities
        on Strava: which race of the season it was, its distance and time, and a link when the
        race is public. Your own text stays as it is.</p>
      <p class="status" id="strava-tagline-status" role="status"></p>
    </div>

    <div class="strava-block">
      <p class="strava-line" id="strava-consent"></p>
      <p class="status" id="strava-consent-status" role="status"></p>
    </div>

    <div class="strava-block">
      <button type="button" class="danger-button" id="strava-disconnect">Disconnect Strava</button>
      <p class="small muted">Revokes this site's access to Strava and deletes every recording
        pulled from it. Races, typed results, reports, photos and FIT recordings stay.</p>
      <p class="status" id="strava-disconnect-status" role="status"></p>
    </div>`;

  renderStravaLines(s);
  renderConsent(s);

  const pullEl = document.getElementById("strava-pull");
  if (pullEl) {
    pullPanel(pullEl, {
      onDone: async () => {
        try {
          renderStravaLines(await api("/strava"));
        } catch {
          // the summary already says what happened; the lines just stay as they were
        }
      },
    });
  }

  const tagline = /** @type {HTMLInputElement} */ (document.getElementById("strava-tagline"));
  const taglineStatus = /** @type {HTMLElement} */ (document.getElementById("strava-tagline-status"));
  tagline.addEventListener("change", async () => {
    taglineStatus.textContent = "Saving…";
    taglineStatus.classList.remove("error");
    try {
      /** @type {StravaStatus} */
      const updated = await api("/strava", { method: "PATCH", body: { tagline: tagline.checked } });
      tagline.checked = updated.tagline;
      taglineStatus.textContent = "Saved";
    } catch (err) {
      tagline.checked = !tagline.checked;
      taglineStatus.textContent = err instanceof ApiError ? err.message : "Couldn't save.";
      taglineStatus.classList.add("error");
    }
  });

  const disconnect = /** @type {HTMLButtonElement} */ (document.getElementById("strava-disconnect"));
  disconnect.addEventListener("click", async () => {
    if (!confirm("Disconnect Strava? This site's access to your Strava account is revoked and "
      + "every recording pulled from Strava is deleted. Races, typed results, reports, photos "
      + "and FIT recordings stay. This can't be undone.")) return;
    const status = /** @type {HTMLElement} */ (document.getElementById("strava-disconnect-status"));
    disconnect.disabled = true;
    status.textContent = "Disconnecting…";
    status.classList.remove("error");
    try {
      await api("/strava/disconnect", { method: "POST" });
      flash("Strava is disconnected.");
      await loadStrava();
    } catch (err) {
      status.textContent = err instanceof ApiError ? err.message : "Couldn't disconnect.";
      status.classList.add("error");
      disconnect.disabled = false;
    }
  });
}

/** The lines a pull changes: when it last ran, and how much of Strava's limit is used. */
function renderStravaLines(/** @type {StravaStatus} */ s) {
  /** @type {HTMLElement} */ (document.getElementById("strava-last-pull")).textContent =
    s.last_pull_at ? dateTime(s.last_pull_at) : DASH;
  const rate = /** @type {HTMLElement} */ (document.getElementById("strava-rate"));
  const used = s.rate_usage;
  const limit = s.rate_limit;
  rate.textContent = used
    ? `Strava requests: ${used[0]}/${limit?.[0] ?? DASH} this 15 min · `
      + `${used[1]}/${limit?.[1] ?? DASH} today`
    : "";
  rate.classList.toggle("hidden", !used);
}

/** Whether visitors may see Strava data on public races, and the way to take it back. */
function renderConsent(/** @type {StravaStatus} */ s) {
  const line = /** @type {HTMLElement} */ (document.getElementById("strava-consent"));
  const status = /** @type {HTMLElement} */ (document.getElementById("strava-consent-status"));
  if (!s.public_consent_at) {
    line.innerHTML = `Showing Strava data to visitors: <span class="muted">not given — you'll be
      asked when you publish a race with Strava data.</span>
      <button type="button" id="strava-agree">Agree now</button>`;
    const agree = /** @type {HTMLButtonElement} */ (document.getElementById("strava-agree"));
    agree.addEventListener("click", async () => {
      if (!confirm("Strava's API terms only allow showing your Strava data to you, unless you "
        + "explicitly agree. Agree that public races may show your Strava-sourced tracks, splits "
        + "and heart rate to visitors? You can withdraw this here at any time.")) return;
      agree.disabled = true;
      status.textContent = "Saving…";
      status.classList.remove("error");
      try {
        /** @type {StravaStatus} */
        const updated = await api("/strava", { method: "PATCH", body: { public_consent: true } });
        renderConsent(updated);
        status.textContent = "Saved.";
      } catch (err) {
        status.textContent = err instanceof ApiError ? err.message : "Couldn't save.";
        status.classList.add("error");
        agree.disabled = false;
      }
    });
    return;
  }
  line.innerHTML = `Visitors may see Strava data on public races since
    <span class="tnum">${esc(dateTime(s.public_consent_at))}</span>.
    <button type="button" id="strava-withdraw">Withdraw</button>`;
  const withdraw = /** @type {HTMLButtonElement} */ (document.getElementById("strava-withdraw"));
  withdraw.addEventListener("click", async () => {
    if (!confirm("Withdraw your consent? Visitors stop seeing Strava data: every public race "
      + "with data from Strava goes back to private. You'll be asked again the next time you "
      + "publish one.")) return;
    withdraw.disabled = true;
    status.textContent = "Saving…";
    status.classList.remove("error");
    try {
      /** @type {StravaStatus} */
      const updated = await api("/strava", { method: "PATCH", body: { public_consent: false } });
      renderConsent(updated);
      status.textContent = "Withdrawn. Public races with Strava data are private again.";
    } catch (err) {
      status.textContent = err instanceof ApiError ? err.message : "Couldn't save.";
      status.classList.add("error");
      withdraw.disabled = false;
    }
  });
}

/** @param {string} key @param {string} label @param {"input"|"textarea"} tag @param {string} value */
function field(key, label, tag, value) {
  const control = tag === "textarea"
    ? `<textarea id="${key}" name="${key}">${esc(value)}</textarea>`
    : `<input id="${key}" name="${key}" type="text" value="${esc(value)}">`;
  return `
    <div class="field">
      <label for="${key}">${esc(label)}</label>
      ${control}
      <p class="status" id="${key}-status"></p>
    </div>`;
}

main();
