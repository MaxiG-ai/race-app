// @ts-check
// The only page reachable while signed out in private mode. No header() call: /api/site
// is 401 for anonymous visitors there. Once the owner's Strava athlete is linked, the
// password form is switched off and "Log in with Strava" is the only way in.

import { api, ApiError } from "./api.js";
import { connectButton, takeStravaParam } from "./strava.js";

/**
 * A same-site path only. Resolved the way the browser will resolve it, so "/\\evil.example"
 * or "/<tab>/evil.example" (both "//evil.example" to a URL parser) can't leave the site.
 */
function safeNext(/** @type {string|null} */ next) {
  if (!next || !next.startsWith("/")) return "/";
  try {
    const url = new URL(next, location.origin);
    return url.origin === location.origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}

const form = /** @type {HTMLFormElement} */ (document.getElementById("login-form"));
const errorEl = /** @type {HTMLElement} */ (document.getElementById("login-error"));

// Back from Strava without a session: say why (not_owner, denied, expired, failed).
const returned = takeStravaParam();
if (returned && !returned.ok) {
  /** @type {HTMLElement} */ (document.getElementById("strava-message")).textContent = returned.message;
}

/** The password form stays unless the site says the owner logs in with Strava. */
async function chooseLogin() {
  /** @type {import("./types.js").Me} */
  let who;
  try {
    who = await api("/auth/me");
  } catch {
    return;
  }
  if (who.login !== "strava") return;
  form.classList.add("hidden");
  const next = safeNext(new URLSearchParams(location.search).get("next"));
  /** @type {HTMLElement} */ (document.getElementById("strava-login-button")).innerHTML =
    connectButton("Log in with Strava", next);
  /** @type {HTMLElement} */ (document.getElementById("strava-login")).classList.remove("hidden");
}
chooseLogin();

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorEl.textContent = "";
  const username = /** @type {HTMLInputElement} */ (document.getElementById("username")).value;
  const password = /** @type {HTMLInputElement} */ (document.getElementById("password")).value;
  const submitBtn = /** @type {HTMLButtonElement} */ (form.querySelector("button[type=submit]"));
  submitBtn.disabled = true;
  try {
    await api("/auth/login", { method: "POST", body: { username, password } });
    const next = safeNext(new URLSearchParams(location.search).get("next"));
    location.href = next;
  } catch (err) {
    errorEl.textContent = err instanceof ApiError ? err.message : "Something went wrong.";
  } finally {
    submitBtn.disabled = false;
  }
});
