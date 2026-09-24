// @ts-check
// The only page reachable while signed out in private mode. No header() call: /api/site
// is 401 for anonymous visitors there.

import { api, ApiError } from "./api.js";

/** A same-site path only: starts with "/" but not "//" (protocol-relative). */
function safeNext(/** @type {string|null} */ next) {
  if (!next) return "/";
  if (!next.startsWith("/") || next.startsWith("//")) return "/";
  return next;
}

const form = /** @type {HTMLFormElement} */ (document.getElementById("login-form"));
const errorEl = /** @type {HTMLElement} */ (document.getElementById("login-error"));

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
