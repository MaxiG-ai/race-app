# Access and visibility

**Priority:** core

## Goal

One owner logs in to add and edit races. Whether anyone else can *read* depends on the
site mode: in a private instance nobody can; in a public instance anyone can browse the
races the owner has published. This file is the only place that decides who sees what.
Every other feature follows it.

## Visibility

| | `SITE_MODE=private` | `SITE_MODE=public` |
|---|---|---|
| Owner, logged in | everything | everything |
| Anyone else | login page only | races with `is_public = true`, with owner-only fields removed ([Redaction](#redaction)) |
| Writes | owner only | owner only |

There are exactly two site modes, and per race exactly two states: public or not.
There is no separate draft state. A race that isn't public *is* the draft.

## Data

```
owner                         exactly one row, created by the CLI
  id (PK)
  username        unique
  password_hash   argon2id
  created_at

session                       opaque server-side sessions; no JWT
  token_hash (PK) sha256 of the cookie value — the raw token is never stored
  created_at
  expires_at
  last_seen_at

site_settings                 exactly one row, editable from the admin UI
  site_title      e.g. "Race history" — page titles, header
  athlete_name    nullable — shown on the public timeline
  about           nullable, short text for the public header
```

On `race` (owned by [races-and-results](races-and-results.md#data)):

```
is_public       bool, default false
published_at    nullable — set when is_public first becomes true
```

`SITE_MODE` is an environment variable, not a database setting. Changing who can read the
whole site is a deployment decision and should be visible in the deployment config.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/auth/login` | anyone, rate-limited | username + password → session cookie |
| `POST` | `/api/auth/logout` | owner | delete the session row, clear the cookie |
| `GET` | `/api/auth/me` | anyone | `{owner: bool, site_mode}`; pages use it to show edit controls |
| `GET` | `/api/site` | reader | `site_settings` for headers and titles |
| `PATCH` | `/api/site` | owner | edit `site_settings` |
| `PATCH` | `/api/races/{id}/visibility` | owner | `{is_public}` |
| `GET` | `/health` | anyone | liveness |

"Reader" means the owner in private mode, and anyone in public mode.

## UI

- `login.html`: the only page available to anonymous visitors in private mode.
- A small owner bar on every page when logged in: "Edit", "Import", "Log out".
  Visitors never see it.
- On the race edit page, a **Public** toggle with a one-line reminder of what a visitor
  will and won't see.
- On the owner's timeline, non-public races get a quiet "not public" marker so it's obvious
  what visitors are missing. The marker is only shown in public mode.

## Rules

### Enforcement lives in two places

1. **Write routes.** Every write endpoint is mounted under a router that requires the
   owner session, applied once at router level. A new write endpoint is protected by
   default; nobody has to remember to add a check.
2. **Read queries.** Every race read goes through one helper,
   `visible_race(db, viewer, race_id)` / `visible_races(db, viewer)`. It adds
   `WHERE is_public` for anonymous viewers in public mode and returns **404** (not 403)
   for hidden races, so the API never confirms a hidden race exists. No endpoint builds a
   race query by hand, and that includes aggregates: `/api/stats/summary` for a visitor
   counts public races only.

Child resources (`/races/{id}/track`, `/laps`, `/compare/{a}/{b}`) resolve their race
through the same helper first. A comparison involving one hidden race is a 404.

The page shells behind [link previews](../architecture.md#link-previews) use the same
helper. A hidden race gets the generic shell, the same as an id that doesn't exist, so
its name never appears in the HTML `<head>`.

### Redaction

Some data is for the owner only, even on a public race. Anonymous responses use
separate, smaller response shapes rather than the owner's shape with fields
nulled out. A field only reaches the public if it is listed in the public shape.

Never public:
- `upload` rows, stored FIT files, `raw_data`, parse errors
- device serials, `device_key`, `gear_device` mappings, the unknown-devices list
- the `completeness` checklist, since it describes the owner's to-do list
- non-primary recordings (visitors see the chosen recording per segment only)
- photo metadata (EXIF, GPS) and `content_hash`; the original photo isn't even kept
  ([photos](photos.md#stripping-metadata))

Public by default (owner-configurable later if it turns out to matter):
- official result, recorded segment totals, lap splits, simplified course track,
  weather, fueling, gear *names*, notes (and their rendered `notes_html`), the
  re-encoded photos

### New data is private

Imports and manually created races start with `is_public = false`. A half-entered race
never goes public by accident. Publishing is always a deliberate click.

### Sessions and cookies

- Cookie: `HttpOnly`, `Secure` (except on `localhost`), `SameSite=Lax`, opaque random
  token (`secrets.token_urlsafe(32)`), with a 30-day sliding expiry.
- CSRF: `SameSite=Lax` plus a required `Content-Type: application/json` or
  `X-Requested-With` header on every write. A cross-site form can't set either.
  Multipart uploads carry the custom header.
- Login is rate-limited per IP (e.g. 5 per minute). The error message doesn't say whether
  the username or the password was wrong.
- Owner account: `uv run python -m app.cli create-owner` and `set-password`. No
  registration and no reset emails; the CLI *is* the reset flow.

## Open questions

- **Heart rate in public.** Some athletes won't want HR public. A site-level
  `public_show_hr` setting is cheap to add; decide whether it's in the first release.
- **GPS privacy zone.** Races rarely start at home, so no trimming by default. Should
  there be an optional "trim first/last N m" for public tracks?
- **Unlisted races.** A race reachable by link but absent from the public timeline. It
  would be a third visibility state, which is why it's not in; recorded here in case it's
  asked for.

## Acceptance

- Private mode: every `/api` read without a session → 401, and every page except
  `login.html` redirects there.
- Public mode, anonymous: timeline, detail, track, laps, stats, compare and the feed only
  ever contain public races; a hidden race id → 404 on every route.
- Public responses contain none of the never-public fields. Test this by asserting the key
  set of each public shape exactly, not by spot-checking.
- Every write route without a session → 401 in both modes. Enumerate all mounted write
  routes in the test so a new one can't slip past.
- New imports and new manual races have `is_public = false`.
- The page shell for a hidden race is identical to the one for a nonexistent id.
