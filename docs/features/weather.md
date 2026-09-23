# Weather

**Priority:** next

## Goal

Every race with a location and a date gets its race-morning conditions filled in
automatically: air temperature, humidity, and a short description.

## Visibility

Public on public races.

## Data

On `race`:

```
weather_temp_c         nullable
weather_humidity_pct   nullable
weather_conditions     nullable, e.g. "clear", "overcast"
weather_fetched_at     nullable
```

Stored per race, not per segment: conditions don't meaningfully change over one morning.

## API

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/api/races/{id}/weather/refresh` | owner | re-fetch, e.g. after correcting the location or date |

## Rules

- Source: **Open-Meteo's historical archive API**. It's free, needs no key, and is called
  with `urllib`. The base URL is configurable (`OPEN_METEO_BASE_URL`).
- Query by race lat/lng and **the race's local date and start hour**. The entered
  `race.date` is the anchor, so a date the owner corrects is respected.
- FIT files never populate a weather value in practice
  ([Trap 10](../reference/fit-files.md#trap-10)), and Garmin's temperature is device
  temperature ([Trap 8](../reference/fit-files.md#trap-8)). The API is the only source.
- Runs after import. If it fails, the error is logged and the fields stay null; the import
  itself never fails because of it.
- No location → nothing to fetch. The race edit page shows "Add a location to fetch weather".

## Acceptance

- The Open-Meteo call is stubbed in tests; the request carries the local date and the right hour.
- A failed request leaves the race importable and the fields null.
