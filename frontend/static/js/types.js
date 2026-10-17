// @ts-check
// JSDoc shapes of the JSON API (backend/app/schemas). Written by hand for now; see the
// open question in docs/architecture.md about generating them from OpenAPI.

/** @typedef {"swim"|"t1"|"bike"|"t2"|"run"} Segment */
/** @typedef {"triathlon"|"swim"|"bike"|"run"} RaceType */

/** @typedef {{id: number, key: string, label: string}} CategoryRef */

/**
 * @typedef {Object} OfficialResult
 * @property {number} total_time
 * @property {boolean} total_is_derived
 * @property {number|null} swim_time
 * @property {number|null} t1_time
 * @property {number|null} bike_time
 * @property {number|null} t2_time
 * @property {number|null} run_time
 * @property {Segment|null} derived_split  the split computed as total minus the others
 * @property {number|null} overall_place
 * @property {number|null} age_group_place
 * @property {boolean} is_prefilled  taken from the recordings; not yet edited or confirmed
 * @property {boolean} is_confirmed  checked against the published results
 */

/**
 * @typedef {Object} RaceUpload  a file on the race page (owner only)
 * @property {number} id
 * @property {string|null} filename
 * @property {"parsed"|"grouped"|"failed"} status  parsed: waiting for its leg to be picked
 * @property {"multisport"|"single_sport"|null} kind
 * @property {"swim"|"bike"|"run"|"other"|null} detected_sport
 * @property {string|null} detected_start_time
 * @property {number|null} detected_distance_m
 * @property {Record<string, any>|null} summary
 * @property {string} uploaded_at
 */

/**
 * @typedef {Object} SegmentOut  the primary recording of one segment
 * @property {number} id
 * @property {Segment} segment
 * @property {boolean} is_derived
 * @property {string} start_time
 * @property {number} elapsed_time
 * @property {number|null} moving_time
 * @property {number|null} distance_m
 * @property {number|null} elevation_gain_m
 * @property {number|null} avg_hr
 * @property {number|null} max_hr
 * @property {number|null} avg_power
 * @property {number|null} normalized_power
 * @property {number|null} avg_cadence
 * @property {number} lap_count
 * @property {number[]|null} bounds  [n, s, e, w]
 * @property {{hr: ZoneSet|null, power: ZoneSet|null}|null} time_in_zone  device-recorded only
 * @property {{id: number, name: string, kind: string}[]} gear
 */

/**
 * @typedef {Object} ZoneSet
 * @property {number[]} high  upper bound of each zone; zone 0 is below zone 1
 * @property {number[]} seconds  one per zone, or one more: the open-ended top zone
 */

/**
 * @typedef {SegmentOut & {is_primary: boolean, upload_id: number|null,
 *   device_key: string|null, avg_temp_c: number|null, rpe_estimated: number|null,
 *   sensors: {manufacturer: string, serial_number: string, label: string|null}[]}} Recording
 */

/**
 * @typedef {Object} Fueling
 * @property {Segment} segment
 * @property {number|null} carbs_g
 * @property {{name: string, quantity: number|null}[]} products
 * @property {string|null} notes
 */

/**
 * @typedef {Object} RaceDetail  public shape; owner-only fields are optional
 * @property {number} id
 * @property {string} name
 * @property {string} date  local date, YYYY-MM-DD
 * @property {RaceType} race_type
 * @property {string|null} location_name
 * @property {number|null} location_lat
 * @property {number|null} location_lng
 * @property {string|null} notes
 * @property {string|null} notes_html  race report, rendered by the backend from escaped text
 * @property {boolean} is_public
 * @property {CategoryRef|null} category
 * @property {{temp_c: number|null, humidity_pct: number|null, conditions: string|null}} weather
 * @property {OfficialResult|null} official_result
 * @property {SegmentOut[]} segments
 * @property {Photo[]} photos
 * @property {Fueling[]} fueling
 * @property {string|null} [published_at]
 * @property {string|null} [weather_fetched_at]
 * @property {{source: "auto"|"manual"|"unclassified", auto_category: CategoryRef|null,
 *   detail: {nearest: string, nearest_label: string, segment: Segment, delta_pct: number}|null,
 *   classified_at: string|null}} [classification]
 * @property {boolean} [marked_complete]
 * @property {Segment[]} [missing_segments]  legs of the race type without a recording
 * @property {boolean} [is_incomplete]  missing legs, and not marked complete
 * @property {RaceUpload[]} [uploads]
 * @property {Recording[]} [recordings]
 * @property {Record<string, {missing?: string[], usable: boolean}>} [completeness]
 */

/**
 * @typedef {Object} RaceListItem
 * @property {number} id
 * @property {string} name
 * @property {string} date
 * @property {RaceType} race_type
 * @property {string|null} location_name
 * @property {boolean} is_public
 * @property {CategoryRef|null} category
 * @property {number|null} total_time
 * @property {"official"|"recorded"|null} times_source
 * @property {Record<Segment, number|null>} segment_times
 * @property {Segment[]} derived_segments
 * @property {Photo|null} cover_photo
 * @property {boolean} [is_incomplete]  owner only
 */

/**
 * @typedef {Object} Lap
 * @property {Segment} segment
 * @property {number} lap_index
 * @property {string} start_time
 * @property {number} total_time
 * @property {number|null} distance_m
 * @property {number|null} elevation_gain_m
 * @property {number|null} avg_hr
 * @property {number|null} max_hr
 * @property {number|null} avg_power
 * @property {number|null} normalized_power
 * @property {number|null} cadence_avg
 * @property {boolean} is_partial
 */

/**
 * @typedef {{segment: Segment, bounds: number[]|null,
 *   points: {lat: number, lng: number, elevation: number|null, t: number,
 *   distance_m: number|null}[]}} TrackSegment
 */

/**
 * @typedef {Object} Checkpoint
 * @property {number} x  cumulative position, 0..1
 * @property {string} label
 * @property {boolean} boundary
 * @property {Segment|null} segment
 * @property {number|null} a  cumulative seconds, reference race
 * @property {number|null} b  cumulative seconds, compared race
 * @property {number|null} gap_s  b − a; positive means b is behind
 */

/**
 * @typedef {Object} Comparison
 * @property {RaceListItem} a
 * @property {RaceListItem} b
 * @property {Checkpoint[]} checkpoints
 * @property {boolean} normalised
 * @property {boolean} coarse
 * @property {{a: Record<Segment, number|null>, b: Record<Segment, number|null>}} segment_times
 * @property {Record<Segment, number|null>} deltas
 * @property {{field: string, a: any, b: any, material: boolean, delta?: number,
 *   delta_pct?: number}[]} conditions
 * @property {{segment: Segment, a: {carbs_g: number|null, g_per_h: number|null,
 *   products: {name: string, quantity: number|null}[]}|null, b: any,
 *   carbs_delta_g: number|null, only_a: string[], only_b: string[]}[]} fueling
 */

/** @typedef {{owner: boolean, site_mode: "private"|"public"}} Me */
/** @typedef {{site_title: string, athlete_name: string|null, about: string|null}} Site */

export {};

/**
 * @typedef {Object} SportTotals  the primary recording's totals; pace is derived
 * @property {number|null} distance_m
 * @property {number} elapsed_time
 * @property {number|null} avg_hr
 * @property {number|null} avg_power
 */

/**
 * @typedef {Object} TrendRace  one row of /api/trends
 * @property {number} id
 * @property {string} name
 * @property {string} date
 * @property {CategoryRef|null} category
 * @property {number|null} temp_c
 * @property {number|null} humidity_pct
 * @property {Record<string, SportTotals|null>} sports  swim, bike, run
 */

/**
 * @typedef {Object} Photo  re-encoded copies only; EXIF never reaches the server's disk
 * @property {number} id
 * @property {string|null} caption
 * @property {number} width
 * @property {number} height
 * @property {boolean} is_cover
 * @property {string} thumb_url
 * @property {string} full_url
 */
