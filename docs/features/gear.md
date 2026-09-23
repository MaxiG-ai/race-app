# Gear

**Priority:** next

## Goal

Track which bike, shoes and wetsuit were used in each race, and how much race distance
each item has built up. The bike is usually detected automatically from sensor serials;
shoes are picked by hand.

## Visibility

Visitors see gear **names** on public races. Serials, device mappings and the
unknown-devices list are owner only ([redaction](access-and-visibility.md#redaction)).
The gear inventory page is owner only.

## Data

```
gear
  id (PK)
  kind                 enum: bike, shoes, wetsuit, other
  name                 e.g. "Speedmax (race)", "Vaporfly 3"
  brand / model        nullable
  purchased_at         nullable
  retired_at           nullable — hidden from pickers, kept in history
  distance_offset_m    default 0 — distance covered before tracking started
  notes                nullable

gear_device                   serial → gear, filled in as devices are named
  id (PK)
  gear_id              FK
  manufacturer         e.g. favero_electronics, shimano
  serial_number        from FIT device_info
  label                nullable, e.g. "power pedals"
  unique (manufacturer, serial_number)

activity_gear                 which gear was used for which recorded segment
  recorded_activity_id FK
  gear_id              FK
  assigned_by          enum: auto_device, manual, inherited
```

Distance per item is a query over `activity_gear` joined to **primary** recordings, plus
`distance_offset_m`. No stored counter to drift.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/gear` | inventory with distance per item |
| `POST` | `/api/gear` | create |
| `PATCH` | `/api/gear/{id}` | edit, retire |
| `GET` | `/api/gear/unknown-devices` | serials seen in imports with no mapping yet |
| `POST` | `/api/gear/{id}/devices` | map a serial to an item; backfills past activities |
| `PATCH` | `/api/activities/{id}` | `{gear_ids}`: manual assignment for a segment |

All owner only.

## UI

- `admin/gear.html`: inventory with distance, retire and rename; a "New sensor seen, which
  gear is this?" prompt for each unknown serial.
- Race edit page: a gear picker per segment, with the last-used shoes offered by default for the run.

## Rules

1. On import, collect every `device_info` serial per session
   ([fit-files](../reference/fit-files.md#paired-sensors-device_info)).
2. Known serial → attach its gear to that segment with `assigned_by = auto_device`.
3. Unknown serial → list it under unknown devices. Once it's named, every past and future
   recording carrying that serial is backfilled.
4. A manual assignment is **never** overwritten by detection.
4a. Detection only attaches gear to the segments its kind belongs to (bike → bike,
   shoes → run, wetsuit → swim, other → any). A watch keeps listing the power pedals
   during the run; that mustn't put run kilometres on the bike.
4b. The recording device itself (`device_info` index `creator`) is `device_key`, not a
   sensor, so it never shows up as an unknown device.
5. **Shoes can't be detected**: no sensor, and Apple files list no paired devices. So the
   run's gear is manual, with the last-used pair as the default.

Gear attaches to `recorded_activity`, not `race`, because mileage is per segment
(bike km on the bike, run km on the shoes).

## Acceptance

- The Edge fixture yields its made-up sensor serials as unknown devices; mapping one
  backfills the bike segment.
- A manual assignment survives a re-import.
- Distance counts primary recordings only, so a second bike recording of the same race
  doesn't double the mileage.
