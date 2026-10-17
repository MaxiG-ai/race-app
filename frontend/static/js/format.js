// @ts-check
// All formatting lives here. Unknown is null and shows as "—", never 0.

export const DASH = "—";
export const SEGMENTS = /** @type {const} */ (["swim", "t1", "bike", "t2", "run"]);
export const SPORTS = /** @type {const} */ (["swim", "bike", "run"]);
export const SEGMENT_LABELS = { swim: "Swim", t1: "T1", bike: "Bike", t2: "T2", run: "Run" };
export const RACE_TYPES = /** @type {const} */ (["triathlon", "swim", "bike", "run"]);
export const RACE_TYPE_LABELS = { triathlon: "Triathlon", swim: "Swim race", bike: "Bike race", run: "Run race" };

/**
 * The segments a race of this type consists of (backend: enums.race_segments).
 * @param {string} raceType
 * @returns {readonly import("./types.js").Segment[]}
 */
export function raceSegments(raceType) {
  return raceType === "swim" || raceType === "bike" || raceType === "run" ? [raceType] : SEGMENTS;
}

/** @param {any} v */
const unknown = (v) => v === null || v === undefined || Number.isNaN(v);

/** Seconds → "1:02:03" or "2:03". */
export function duration(/** @type {number|null|undefined} */ s) {
  if (unknown(s)) return DASH;
  const total = Math.round(/** @type {number} */ (s));
  const sign = total < 0 ? "−" : "";
  const a = Math.abs(total);
  const h = Math.floor(a / 3600);
  const m = Math.floor((a % 3600) / 60);
  const sec = String(a % 60).padStart(2, "0");
  return h ? `${sign}${h}:${String(m).padStart(2, "0")}:${sec}` : `${sign}${m}:${sec}`;
}

/** Signed delta: "+2:13", "−1:09", "±0:00". */
export function delta(/** @type {number|null|undefined} */ s) {
  if (unknown(s)) return DASH;
  if (Math.round(/** @type {number} */ (s)) === 0) return "±0:00";
  const text = duration(s);
  return /** @type {number} */ (s) > 0 ? `+${text}` : text;
}

/** Metres → "40.6 km" (or "750 m" below 1 km). */
export function distance(/** @type {number|null|undefined} */ m) {
  if (unknown(m)) return DASH;
  const v = /** @type {number} */ (m);
  return v < 1000 ? `${Math.round(v)} m` : `${(v / 1000).toFixed(1)} km`;
}

export function elevation(/** @type {number|null|undefined} */ m) {
  return unknown(m) ? DASH : `${Math.round(/** @type {number} */ (m))} m`;
}

/**
 * Pace is derived on read. Swim: per 100 m; run: per km; bike: km/h.
 * @param {"swim"|"bike"|"run"|string} sport
 * @param {number|null|undefined} metres
 * @param {number|null|undefined} seconds
 */
export function pace(sport, metres, seconds) {
  if (unknown(metres) || unknown(seconds) || !metres || !seconds) return DASH;
  const m = /** @type {number} */ (metres);
  const s = /** @type {number} */ (seconds);
  if (sport === "bike") return `${((m / s) * 3.6).toFixed(1)} km/h`;
  if (sport === "swim") return `${duration((s / m) * 100)} /100 m`;
  if (sport === "run") return `${duration((s / m) * 1000)} /km`;
  return DASH;
}

/** Pace as a number for charts: seconds per km (run), per 100 m (swim), km/h (bike). */
export function paceValue(/** @type {string} */ sport, /** @type {number|null} */ metres,
  /** @type {number|null} */ seconds) {
  if (!metres || !seconds) return null;
  if (sport === "bike") return (metres / seconds) * 3.6;
  if (sport === "swim") return (seconds / metres) * 100;
  return (seconds / metres) * 1000;
}

export function number(/** @type {number|null|undefined} */ v, unit = "", digits = 0) {
  if (unknown(v)) return DASH;
  return `${(/** @type {number} */ (v)).toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

/** "2026-06-21" → "21 Jun 2026". Local dates are formatted without timezone shifts. */
export function date(/** @type {string|null|undefined} */ iso) {
  if (!iso) return DASH;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
}

/** Parse "1:02:03", "62:03" or "3723" into seconds; "" → null; invalid → NaN. */
export function parseDuration(/** @type {string} */ text) {
  const t = text.trim();
  if (!t) return null;
  if (!/^\d+(:\d{1,2}){0,2}$/.test(t)) return NaN;
  return t.split(":").map(Number).reduce((acc, part) => acc * 60 + part, 0);
}

/** Escape text for innerHTML templates. */
export function esc(/** @type {any} */ value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}
