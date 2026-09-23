// @ts-check
// The only module that talks to the backend. Every request carries X-Requested-With,
// which the backend requires on writes (CSRF, see access-and-visibility.md).

export class ApiError extends Error {
  /** @param {number} status @param {string} message */
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * @param {string} path  e.g. "/races/3"; "/api" is prepended
 * @param {{method?: string, body?: any, form?: FormData}} [options]
 * @returns {Promise<any>}
 */
export async function api(path, { method = "GET", body, form } = {}) {
  /** @type {Record<string, string>} */
  const headers = { "X-Requested-With": "fetch" };
  /** @type {BodyInit | undefined} */
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(`/api${path}`, { method, headers, body: payload, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("/auth/")) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new ApiError(401, "Log in to continue.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ")
      : `Request failed (${response.status}).`;
    throw new ApiError(response.status, message);
  }
  return data;
}

/** @type {Promise<import("./types.js").Me> | null} */
let mePromise = null;

/** Who is looking: cached for the page's lifetime. */
export function me() {
  mePromise ??= api("/auth/me");
  return mePromise;
}
