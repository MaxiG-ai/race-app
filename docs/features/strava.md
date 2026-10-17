# Strava

**Priority:** deferred

## Goal

Backfill races from Strava for athletes whose older FIT files are gone.

## Why it isn't the main source

- **No multisport activity type.** A triathlon arrives as 3+ unlinked activities.
- No reliable "race" flag: `workout_type` exists for runs only and depends on manual tagging.
- The data is thinner than FIT: no developer fields, coarser streams.
- OAuth is needed for every athlete; the rate limits (100 per 15 min, 1 000 per day) are fine for one owner.
- "Export original" returns the *uploaded* file, which may be a multisport file covering
  several activities ([fit-files](../reference/fit-files.md#strava-exports)). Downloading
  originals and uploading them through [import](import.md) already works today.

## Design when built

Reuse the grouping path; don't add a parallel one. Fetch activities by date range → create
`upload` rows with `source = strava`, no file, deduplicated on `external_id` → the owner
picks activities onto a race page, and they fill legs exactly like dropped files
([grouping](grouping.md)). The only Strava-specific code is the OAuth flow and the
activity → `upload` mapping. The OAuth tokens belong to the owner and are stored
server-side, never sent to the browser.
