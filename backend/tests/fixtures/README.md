# Test fixtures — real, unanonymized race data

**This repository must stay private.** These files are genuine race recordings and
contain personal data:

- Athlete and device names embedded in the FIT binaries
  (e.g. `Maximilian's Apple Watch`, `Watch6,15`).
- `user_profile` messages with weight, height, age and gender.
- Full GPS tracks whose start and finish points sit near home.
- Stable hardware serial numbers for the Edge head unit, power meter, groupset and
  HR strap.

They are kept as-is deliberately. Anonymizing them would defeat their purpose: the
sensor serials are exactly what the gear-detection tests (§3 Gear detection) exercise,
and the GPS tracks are what map rendering and bounding-box fitting are tested against.
Every correction in the plan's §3 came from real vendor quirks that synthetic files
would not reproduce.

Before making this repository public, these files must be removed from the history —
not merely deleted in a later commit.

## Contents

| File | Device | Shape | Why it's here |
|---|---|---|---|
| `multisport_2026-07-11.fit` | Apple Watch | 5 sessions, 9 829 records | Baseline multisport; drops the trailing partial bike lap |
| `multisport_2026-08-09.fit` | Apple Watch | 5 sessions, 9 413 records | Second Apple sample; T2 barely recorded (20 m / 7.7 s) |
| `multisport_2024-08-03_garmin.fit` | Garmin | 5 sessions, 6 389 records | Bike power + NP, 500 m swim laps, smart recording, temperature, elapsed ≠ moving, one orphan record |
| `single_bike_2026-06-21_edge540.fit` | Garmin Edge 540 | 1 cycling session | Single-sport grouping path; paired sensor serials for gear detection |
| `strava_export_2026-06-21_multisport.fit` | Apple Watch, via Strava | 5 sessions | Strava re-export; byte-identical to the file its "run" download also returns — the dedupe case |

The last two are the same race, recorded by two devices: the Edge covers the bike leg
with power, the watch covers everything. That race is the `is_primary` test case.
