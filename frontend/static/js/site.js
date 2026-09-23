// @ts-check
// Owner-only site settings: site_title, athlete_name, about. Autosave on blur, one
// changed field per request (docs/features/access-and-visibility.md).

import { api, ApiError } from "./api.js";
import { header, fail } from "./layout.js";
import { esc } from "./format.js";

/** @typedef {import("./types.js").Site} Site */

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
