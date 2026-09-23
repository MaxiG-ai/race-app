# FIT files — what they actually contain

Facts about real FIT files, collected from five real race recordings during v1:
three multisport exports (two Apple Watch, one Garmin), one single-sport Garmin Edge 540
bike recording, and one Strava re-export. **Those files are not in the repository.** They
were personal data and have been replaced by synthetic fixtures (see
[Testing](#testing-without-the-originals)). This page is what remains of them, and it's
what the synthetic fixtures must reproduce.

The vendor split matters more than any single file's quirks.

## The files that were observed

| | Apple `2026-07-11` | Apple `2026-08-09` | Garmin `2024-08-03` |
|---|---|---|---|
| Records | 9 829 | 9 413 | 6 389 |
| Laps | 19 | 21 | 25 |
| Swim | 1 572 m / 28:59 | 1 548 m / 31:12 | 1 645 m / 20:42 |
| T1 | 552 m / 3:36 | 105 m / 2:27 | 273 m / 3:33 |
| Bike | 33 746 m / 1:13:08, +558 m | 40 570 m / 1:09:39, +22 m | 40 868 m / 1:12:35, +327 m |
| T2 | 218 m / 2:00 | 20 m / 0:08 | 27 m / 1:10 |
| Run | 9 172 m / 56:03, +166 m | 9 933 m / 53:24, +48 m | 9 239 m / 46:43, +59 m |

## What holds across both vendors

- **One multisport FIT = one race**, with 5 `session` messages in order: swim
  (open_water), transition (T1), cycling, transition (T2), running. The `record` stream is
  split by session boundaries; the importer does not expect 5 separate uploads.
- **`record`** messages carry the position stream (99.8–100 % GPS coverage, including the
  open-water swim).
- **`session`** carries `total_ascent` and an `nec`/`swc` bounding box, which is enough to
  fit the map viewport without reading the point stream.
- **Runs auto-lap at 1 km, bikes at 5 km**, consistently.
- **No nutrition or carbs data anywhere**, on either vendor. Fueling is entered by hand.
- **`avg_speed` is unreliable on laps.** It's absent on every Garmin lap and on Apple
  swim laps. Derive pace from distance ÷ time.
- **Timestamps are UTC.** See [Trap 11](#trap-11).

## Where the vendors diverge

This is what breaks naive importers. **Nothing device-specific may be assumed from the
sport alone.**

| | Apple Watch | Garmin |
|---|---|---|
| `manufacturer` | `development` (the converter rewrites it) | `garmin` |
| Bike power | absent (no power meter) | **`avg_power` 225 W, `normalized_power` 250 W**, plus cycling dynamics (L/R power phase, torque effectiveness, pedal smoothness, PCO) |
| Run power | **295–307 W** (estimated by the watch) | absent |
| Swim laps | 1 lap for the whole swim | **4 × 500 m laps** |
| Trailing partial bike lap | **dropped** | present; all laps reconcile exactly |
| Sampling | uniform 1 Hz | **smart recording**, variable: run 699 points over 2 803 s (~0.25 Hz), bike 4 349 over 4 355 s (1 Hz) |
| Temperature | absent | **on 100 % of records**, 24–35 °C |
| Cadence | 35 % of records in one file, absent in the other | 100 % of records |
| Developer fields | `SESSION UUID`, `INDOOR`, `ACTIVITY TYPE`, `RPE ESTIMATED`, `AVG METs`, `WEATHER HUMIDITY` | none |
| `elapsed` vs `timer` | identical (no pauses) | **differ** (bike 4 354.9 s vs 4 348.9 s) |

## Traps

<a id="trap-1"></a>
**1. Never derive segment totals by summing laps.** Apple drops the bike's trailing
partial lap: 6 × 5 000 m against a 33 746 m session, which understates the bike by 3.7 km.
Garmin includes it and reconciles exactly, so testing against a Garmin file, or against
Apple's *run*, would suggest lap-summing is safe. It isn't. Session totals are
authoritative; laps carry pacing shape only.

<a id="trap-2"></a>
**2. Power presence is a property of the device, not the sport.** Apple gives run power
and no bike power; Garmin gives bike power (with `normalized_power`) and no run power.
Every segment carries `avg_power`, and the UI shows a series only where data exists.

<a id="trap-3"></a>
**3. Consecutive session windows overlap sub-second.** One session ends 11:51:42.054 and
the next starts 11:51:42. Inclusive bucketing double-counts, so use half-open
`[start, end)`. Half-open windows alone are not enough, though: also clamp each session's
end to the next session's start. The overlap is between one session's *derived* end and
the next one's *recorded* start. The clamp does nothing wherever a real gap exists, so
Trap 4's orphans survive it. A record that still matches two windows is a bug and fails
the import.

<a id="trap-4"></a>
**4. Records can fall outside every session window.** The Garmin file has one orphan in
the 1.1 s gap between bike end and T2 start. The Strava re-export has one too: a trailing
run record a fraction of a second past the run's computed end. Orphans are ordinary. They
are dropped and counted, never fatal.

<a id="trap-5"></a>
**5. Store elapsed *and* moving time.** Garmin's differ (pauses exist); Apple's don't. A
single time column silently picks one and loses the other.

<a id="trap-6"></a>
**6. Transitions are real segments but poorly recorded.** T1 ranges 105–552 m; the Apple
T2 of 20 m / 7.7 s is essentially a miss. Recorded transitions are not ground truth,
which is why official-vs-recorded is the centrepiece of the race detail view.

<a id="trap-7"></a>
**7. Lap counts per sport vary by vendor**, so chart shape can't be hardcoded per sport:
an Apple swim is one lap (a summary tile, not a line); a Garmin swim is four.

<a id="trap-8"></a>
**8. Garmin temperature is device temperature, not air temperature.** A sun-exposed wrist
sensor read up to 35 °C (mean 29.3 °C). Store it, but air temperature comes from the
weather API.

<a id="trap-9"></a>
**9. Field names are the `enhanced_` variants** (`enhanced_speed`, `enhanced_altitude`).
Altitude is 100 % absent during the open-water swim and 100 % present everywhere else, on
every file from both vendors. It is one contiguous hole per race, and only the swim needs
gap handling in elevation profiles.

<a id="trap-10"></a>
**10. Developer fields are Apple-only and sparse.** `AVG METs` is declared but never
populated, `RPE ESTIMATED` appears in one file of two, and `SESSION WEATHER HUMIDITY` is
never populated at all. Weather comes from the API in practice.

<a id="trap-11"></a>
**11. The race date is local, the timestamps are not.** Apple filenames encode *local*
time (`...131906` against a `time_created` of 11:19:06 UTC, i.e. CEST +2). `race.date`
must be derived from the start location's timezone, or an early start lands on the wrong
day. Garmin files that carry `timestamp_correlation` give the UTC↔local offset directly.

## Present and unused

- `time_in_zone`: HR-zone distribution per session, both vendors.
- Garmin `gps_metadata`: 8 963 messages, but no position fields beyond `record`.
- Garmin `split` messages: run/walk detection (`rwd_run`/`rwd_walk`), not multisport
  splits.
- ~9 500 Garmin messages across undecoded `unknown_*` types. They land in `raw_data`
  untouched.
- Edge 540: a `sport` message named `TRIATHLON` (the activity profile).

## Paired sensors (`device_info`)

A Garmin head unit lists every paired sensor by manufacturer and serial. The Edge 540
file carried the head unit itself, `favero_electronics` (Assioma power pedals),
`shimano` product 12917 (Di2 groupset) and `polar_electro` (HR strap). Serials are
stable across rides, which is what makes [gear detection](../features/gear.md) work.
Apple files list only the watch: no paired devices at all.

## Strava exports

Downloading three activities (`Schwimmen`, `Rad`, `Lauf`) of one race from Strava gave:

| File | Content |
|---|---|
| swim download | the **full Apple multisport file**, all 5 sessions |
| run download | **byte-identical to the swim download** |
| bike download | a **different device**: Garmin Edge 540, 1 cycling session |

1. **Strava's "export original" returns the uploaded file, not a per-activity file.**
   Three downloads can be one, two or three distinct files, with no way to tell from the
   filename. Content-hash dedup catches it; see [import](../features/import.md#rules) for
   what the UI must say.
2. **The same segment can be recorded by two devices at once.**

   | | Apple Watch (multisport) | Garmin Edge 540 |
   |---|---|---|
   | Window | 07:41:45 – 08:15:58 | 07:43:03 – 08:16:23 |
   | Distance | 19 992 m | 19 634 m |
   | Power | — | **247 W avg, NP 261** |
   | Cadence | 2 (garbage) | **84** |

   Neither is wrong; the Edge is the better record of that leg. See
   [grouping](../features/grouping.md#multiple-recordings-of-one-segment).

<a id="library"></a>
## Library

**`fitdecode`** parsed every file without malformed-frame errors, including Garmin's
undecoded proprietary messages.

## Testing without the originals

The repository ships **synthetic** FIT files, each built to reproduce specific traps
above. They're written by a script in `backend/tests/fixtures/` using **`fit-tool`**, a
dev-only dependency that is never installed in the runtime image. Each fixture is small
and has one job:

| Fixture | Reproduces |
|---|---|
| `apple_multisport.fit` | 5 sessions; dropped trailing bike lap (1); run power, no bike power (2); single swim lap (7); dev fields (10); swim with no altitude (9) |
| `garmin_multisport.fit` | bike power + NP (2); 4 swim laps (7); smart recording; elapsed ≠ timer (5); device temperature (8); one orphan record (4) |
| `overlap_boundary.fit` | sub-second session overlap (3) |
| `edge_single_bike.fit` | single cycling session; `device_info` with made-up sensor serials; `timestamp_correlation` (11) |
| `early_start_local_date.fit` | UTC date ≠ local date (11) |

Real recordings stay useful as a second line of defence: drop your own files into
`backend/tests/fixtures/private/` (gitignored). The real-file suite runs when files are
present and skips otherwise.

The generated `.fit` files are committed alongside the script, so running the tests
doesn't need `fit-tool`. Only changing a fixture does. After regenerating, check each
fixture with `fitdecode`, the parser the app itself uses. That catches any disagreement
between the writer and the reader.
