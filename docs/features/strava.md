# Strava

**Priority:** next

## Goal

The owner logs in with Strava, presses **Pull from Strava**, and every activity they
tagged as a race arrives as a race, with its legs filled exactly as dropped FIT files would
fill them. A triathlon recorded as a swim, a ride and a run becomes one race. Each pulled
activity gets a short tagline appended to its Strava description: which race of the season
it was, its distance and time, and a link to the race page once it is public.

FIT files stay the richer source ([vision](../vision.md#principles)): Strava has no
sensor serials, no time-in-zone messages and no developer fields. When both exist for a
leg, the FIT recording stays primary and the Strava activity is a second recording.

## Visibility

| | Owner | Visitor (public mode) |
|---|---|---|
| Login, connection, pull button, settings | yes | never |
| Pulled races | like any race | only once published, which asks for consent once ([Publishing](#publishing)) |
| "View on Strava" link per Strava leg | yes | yes, on published races |
| Tokens, scopes, rate usage, the pulled-activity list | status only, never tokens | never |

## Data

```
strava_account                exactly one row while connected
  id (PK, always 1)
  athlete_id          bigint, unique
  athlete_name        nullable
  access_token        6 h; refreshed 5 min before it expires
  refresh_token       rotates: the newest one is stored at once
  expires_at
  scope               what the athlete granted, comma-separated
  connected_at
  last_pull_at        nullable; the next pull lists activities from 60 days before it
  needs_reconnect     bool

strava_activity               one row per race activity a pull handled
  activity_id (PK)    bigint
  name, sport_type, start_time
  race_id             FK nullable, SET NULL — null: the owner deleted its race
  state               imported | skipped
  message             why it was skipped, or what the import said
  tagline             the line last written to its description; null: none yet
  tagline_at
  seen_at
```

On existing tables:

- `owner.strava_athlete_id` — bigint, unique, nullable. Set: the owner logs in with this
  athlete and the password login is off.
- `site_settings.strava_tagline` — bool, default true.
- `site_settings.strava_public_consent_at` — nullable timestamp ([Publishing](#publishing)).
- `upload` rows with `source = strava`: `external_id` is the activity id, `filename` its
  name, `content_hash` null. The stored original is `<upload id>.strava.json`: the
  activity, laps and streams as Strava returned them, so `cli reimport` re-maps without
  calling Strava ([import](import.md#storage-and-limits)).
- `recorded_activity.source = strava` on every recording made from one, and on a
  transition derived after a Strava leg.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `GET` | `/auth/strava?next=` | anyone | redirect to Strava's consent screen; sets the `state` cookie |
| `GET` | `/auth/strava/callback` | anyone, `state`-checked | log in, connect, or turn away |
| `GET` | `/api/strava` | owner | connection status, granted scopes, rate usage, settings |
| `PATCH` | `/api/strava` | owner | `{tagline, public_consent}`; withdrawing unpublishes races with Strava data |
| `POST` | `/api/strava/pull` | owner | `{everything}` → races made or added to, skipped activities, taglines written; 409 while another pull runs |
| `POST` | `/api/strava/disconnect` | owner | revoke at Strava, delete Strava data → `{revoked, message}` |
| `GET` | `/api/strava/webhook` | anyone | Strava's subscription handshake (`hub.challenge`) |
| `POST` | `/api/strava/webhook` | anyone | push events; each one is checked against the API before acting |

The two `/auth/strava` routes are browser redirects, so they live outside `/api` and the
OpenAPI schema. Without Strava credentials, everything but `GET /api/strava` (which says
`enabled: false`) is 404. `PATCH /api/races/{id}/visibility` takes `strava_consent`
([Publishing](#publishing)). `GET /api/auth/me` says `login: password | strava`.

CLI: `strava-unlink` (password login back on, Strava data deleted) and
`strava-subscribe <callback-url>` (create the webhook subscription).

## UI

- **Login page**: once Strava is linked, only an orange "Log in with Strava" button; the
  password form is gone. Messages for a refused athlete, a cancelled consent, an expired
  attempt.
- **Site page, Strava section**: connect / reconnect, "Connected as …" with a link to the
  athlete on Strava, missing-scope warnings, last pull, the pull button with "search all
  of Strava", the tagline switch, the consent line with "Withdraw", rate usage,
  "Disconnect" (says that Strava recordings will be deleted).
- **Timeline**, owner only, when connected: the **Pull from Strava** button above the
  feed; the result lists each race (new or added to) and each skipped activity with why.
- **Race page**: Strava files are marked "from Strava" with "View on Strava"; publishing a
  race with Strava data asks for consent once.
- **Public race page**: "View on Strava" beside each Strava leg, as the brand guidelines
  require for Strava data.

## Rules

### Login

Strava has no OpenID Connect: the authorization-code flow *is* the login, and the athlete
is `athlete.id` from the token response. One flow serves login and connect, and asks for
`read,activity:read_all,activity:write` with `approval_prompt=auto`, so logging in again
skips the consent screen. The callback's `scope` parameter is what was granted: athletes
can untick scopes, so the pull and the tagline each check theirs.

- The `state` value is random, kept in an HttpOnly, SameSite=Lax cookie scoped to
  `/auth/strava` for ten minutes, and compared in constant time. `next` must be a path on
  this site: a backslash or a control character, which browsers turn into `//`, sends the
  browser to `/` instead.
- Owner logged in (password or Strava) → **connect**: the athlete is linked to the owner.
  A different athlete than the one already linked is refused.
- Not logged in → the linked athlete gets an owner session. On a fresh instance (no
  owner), the athlete named in `STRAVA_OWNER_ATHLETE_ID` claims it: an owner without a
  usable password is created. Anyone else is **turned away, and their token revoked at
  once**, so the site never holds a stranger's access.
- Once linked, `POST /api/auth/login` is 403: Strava *replaces* the password. The way
  back is the CLI (`strava-unlink`, then `set-password`).
- Without `STRAVA_CLIENT_ID`/`STRAVA_CLIENT_SECRET` everything here is off and the
  password login is the only one.

### Finding races

1. List `/athlete/activities` (200 per page) since 60 days before the last pull, or all of
   it with "search all of Strava". A race tagged weeks later is still found.
2. A **race** is `workout_type` 1 (run) or 11 (ride). The API reports the race flag on runs
   and rides only.
3. **Chains.** Strava has no multisport activity ([grouping](grouping.md)), so swim, bike
   and run legs, in that order, each starting at most 30 min after the previous one ends,
   are one race. Its legs are the race-tagged ones, plus a swim right before a
   race-tagged leg: a swim *can't* say it is a race through the API. An untagged ride or
   run could have, and didn't: a ride to the start stays out. Other sports (walks,
   e-bikes, workouts, transition activities) never join.
4. Activities the pull handled before are skipped. A chain's new legs go to the race its
   earlier legs went to; else to a race that already has a recording overlapping it (the
   FIT file of the same race); else to a **new race**: one sport makes that single-sport
   race, more is a triathlon. It is named from the words the legs' names share
   ("Lake Sprint Swim", "Lake Sprint Bike" → "Lake Sprint") and starts private. Before
   making one, a race of the chain's type on its day with no recordings at all is used:
   after a disconnect and a reconnect, the race keeps its name, result and photos instead
   of getting a twin.
5. A deleted race isn't pulled again: its activities stay in `strava_activity`.
6. An activity the race refuses (a leg its type doesn't have, a recording hours away) is
   listed as skipped with why, but not remembered: once the owner changes the race, the
   next pull adds it. An activity whose Strava data can't be read is remembered as skipped.
7. One pull at a time: a second one is 409 until the first finishes.

### Mapping

Each activity becomes one single-sport `ParsedFile` (`services/strava_map.py`) and goes
through `imports.place`, the code dropped files use: legs, primaries, derived
transitions, classification, weather and the prefilled result all work unchanged.

- Totals are the activity's `elapsed_time`, `moving_time`, `distance` and
  `total_elevation_gain`, never lap sums ([Trap 1](../reference/fit-files.md#trap-1)).
  A distance or climb of 0 is unknown (null); swims have no climb.
- **Power only when measured.** Strava estimates watts for rides without a power meter;
  `device_watts = false` means an estimate, which is not a measurement and stays null
  ([Trap 2](../reference/fit-files.md#trap-2)).
- The race date is `start_date_local`, the athlete's wall clock, though it ends in `Z`
  ([Trap 11](../reference/fit-files.md#trap-11)).
- The track comes from the `time`, `latlng`, `altitude` and `distance` streams, in the FIT
  parser's shape, so maps, pacing and comparison work. Laps carry pacing shape only; a
  last lap well short of the others is partial.
- No `device_key` and no sensors: `device_name` is a product name, not a serial, so gear
  stays manual ([gear](gear.md)). No time in zone.
- **FIT stays primary**, whichever arrived first: a FIT recording dropped on a leg that a
  Strava activity filled takes the primary flag from it.

### The tagline

`🏁 Race #7 of 2026 · Kurzdistanz · 2:31:04 · https://race.example.com/races/12`: its
place among the year's races by date, the category, the official (or prefilled) total,
and the link only when the site and the race are public and `PUBLIC_BASE_URL` is set.

- Written at the end of every pull, to every pulled activity whose line changed: a
  result typed in or a race published updates it next time. Unchanged lines cost nothing.
- Read-modify-write: Strava's `description` is replaced whole, so the athlete's text is
  kept and only a line starting `🏁 Race #N of YYYY` is replaced. Never duplicated. An
  activity this pull just fetched uses the description it read then.
- Needs `activity:write`; the site setting switches it off. The athlete asked for it, so
  it isn't "unsolicited promotion" (API Policy §5.12).

### Publishing

Strava's API Agreement (2026) lets a user's Strava data be shown **only to that user**
without their explicit consent. Making a race with Strava recordings public therefore
asks the owner, once, to agree that visitors may see their Strava-sourced tracks, splits
and heart rate (`PATCH …/visibility` answers 409 until `strava_consent: true` is sent).
The consent is stored with its date. It can also be given on the Site page, and withdrawn
there: withdrawing sends every public race with Strava data back to private. A pull never
adds Strava data to a race that is already public without consent: those activities are
listed as waiting, and the next pull tries them again. Public pages show "View on Strava"
beside each Strava leg, the comparison page included.

### Revocation and deletion

The Agreement requires deleting Strava data after the athlete revokes access.

- **Disconnect**: revoke at `/oauth/revoke` (the legacy `/oauth/deauthorize` as fallback,
  with a token renewed first so it hasn't expired), then delete every Strava upload, its
  recordings and stored original, the pulled-activity list and the tokens. Races, results,
  reports, photos and FIT recordings stay; transitions are re-derived from what's left
  (where the legs left can't make them, the derived ones go and each leg keeps a primary).
  When Strava doesn't confirm the revocation, the data still goes and the owner is told to
  remove the app in their Strava settings.
- A refresh refused by Strava (400 or 401 from the token endpoint) means access was
  revoked on strava.com: the same deletion happens at once. Logging in with Strava again
  reconnects. A timeout, a 429 or a 5xx revokes nothing: the pull reports it and the data
  stays. Neither does a refusal that names the application (`client_id`/`client_secret`):
  that is a deployment mistake, reported as such. The token row is locked while refreshing, so two requests refreshing at once
  (a pull and a webhook check) don't send the same rotated refresh token twice.
- A 401 from a data call (access revoked while the access token still had time left)
  renews the token at once, so a refusal deletes the data then and not hours later. If
  Strava can't be reached for that, `needs_reconnect` is set.
- Logging in still works after a disconnect: Strava stays the login, and the next
  login reconnects. The pulled-activity list went with the rest, so races the owner had
  deleted can come back on the next pull.
- Webhook checks follow the same path: a 401 renews first and only a refused renewal
  deletes; an activity deleted on Strava loses its recording even when the legs left
  behind can't make derived transitions.

### Webhook

Optional, for public instances (`STRAVA_WEBHOOK_VERIFY_TOKEN`, then
`cli strava-subscribe https://<domain>/api/strava/webhook`). The POST answers 200
immediately and works afterwards (Strava allows 2 s). Events aren't signed, so none is
believed on its own: a deauthorization deletes data only if the API then refuses the
token, and an activity delete removes its recording only if the API says 404. A delete
event for an activity this site doesn't hold is dropped without asking Strava, so forged
ones can't spend the rate limit. Create and update events are ignored: pulling stays the
owner's button.

### API usage

- Standard library HTTP, one replaceable function (`strava_api.http`), like weather.
- The token always goes in the `Authorization` header (required from 2027-06-01).
- Rate limits: every response's `X-RateLimit-*` / `X-ReadRateLimit-*` headers are read; a
  pull stops 10 requests short of a limit and says so, and the next pull continues where
  it stopped (`last_pull_at` only moves after a complete pull; a stopped "search all of
  Strava" says to keep it ticked). A reading stops counting once its window has reset: the
  15-minute one on the quarter hour, the daily one at 00:00 UTC. Any other Strava error
  mid-pull stops it the same way, with the races already filed listed. A first pull costs
  1 request per 200 activities plus 3 per race leg (detail, laps, streams), plus 2 per
  tagline. 429 is reported, never retried in a loop.
- `STRAVA_API_BASE` exists for Strava's move to `api-v3.strava.com` (2027).

## Open questions

- **Swim races alone.** A swim-only race can't be found: the API has no race flag on
  swims. Drop its FIT file, or ask Strava whether the 2026 "Competition" tag is readable.
- **The 7-day cache clause** (Policy §6.2) reads as if Strava data must not be kept longer
  than 7 days. Pulled races are kept until disconnect. Ask Strava how it applies to an
  athlete's own archive; until then the stored originals are the athlete's own copy.
- **Official button artwork.** The guidelines ask for Strava's own "Connect with Strava"
  asset; it couldn't be fetched while building this, so the button is drawn in CSS with
  the brand orange. Vendor the official SVG.
- **Push-driven pulls.** Webhook create events could pull automatically; left out so a
  pull stays a deliberate click.

## Acceptance

- Mapping: totals are the activity's, not lap sums; estimated watts are null and measured
  ones kept; 0 distance is null; the date is `start_date_local`'s.
- A tagged ride and run with an untagged swim right before become one private triathlon
  with derived T1/T2; a warm-up run and a ride to the start stay out; legs hours apart
  are separate races.
- A second pull imports nothing; a deleted race doesn't come back; a Strava ride over a
  FIT race's bike leg becomes its second recording, and the FIT one stays primary.
- Login: the linked athlete gets a session; a stranger is refused and revoked; a forged
  `state` is refused; `next` can't leave the site; the configured athlete claims a fresh
  instance; the password login is 403 once linked.
- Tokens are never in a response; an expiring token is refreshed and the newest refresh
  token stored; a refused refresh deletes the Strava data and keeps the race; a refresh
  that times out, is rate-limited or gets a 5xx deletes nothing, nor do two at once.
- The tagline keeps the athlete's text, is written once, changes when the result does,
  links only when public, and is off when switched off.
- Publishing a race with Strava data is 409 until consent, once; public legs carry
  `strava_url`.
- Disconnect revokes and deletes only Strava data. A pull stops before the rate limit.
- Webhook: the handshake checks the token; a forged deauthorization or delete event
  changes nothing.
