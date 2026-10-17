"""Two races side by side (docs/features/comparison.md#rules).

Race `a` is the reference, pinned at zero; every gap is `b − a` in seconds, so a
positive gap means `b` is behind. Checkpoint times inside a segment are scaled so the
segment ends exactly at its split time: the gap at each boundary *is* the cumulative
split delta, which is the invariant the Δ strip relies on.
"""

from __future__ import annotations

import bisect
import statistics
from typing import Any

from app.models import Race, RecordedActivity
from app.models.enums import SEGMENTS, SPORT_SEGMENTS, ClassificationSource, Segment
from app.services import results

LIKE_FOR_LIKE = 0.05  # distances within 5 %: checkpoints at absolute distances
UNCLASSIFIED_MATCH = 0.10  # unclassified races compare when every leg is within 10 %
TRANSITION_WIDTH = 0.02  # share of the x axis a transition gets, so it stays visible
BOUNDARY_LABELS = {"swim": "Swim", "t1": "T1", "bike": "Bike", "t2": "T2", "run": "Finish"}


class NotComparable(ValueError):
    pass


def segment_times(race: Race) -> dict[Segment, int | None]:
    """Official split where known (the remainder counts), else the primary recording."""
    result = race.official_result
    official = {s: result.split(s) for s in SEGMENTS} if result else {}
    if result and (remainder := results.remainder_split(race, result)):
        official[Segment(remainder[0])] = remainder[1]
    primaries = race.primary_activities()
    return {
        s: official.get(s)
        if official.get(s) is not None
        else getattr(primaries.get(s), "elapsed_time", None)
        for s in SEGMENTS
    }


def _distances(race: Race) -> dict[Segment, float | None]:
    primaries = race.primary_activities()
    return {s: getattr(primaries.get(s), "distance_m", None) for s in SEGMENTS}


def _sports(race: Race) -> list[Segment]:
    """The race type's measured legs: swim, bike and run, or the one of a single-sport race."""
    return [s for s in race.segments() if s in SPORT_SEGMENTS]


def _close(x: float | None, y: float | None, tolerance: float) -> bool:
    return x is not None and y is not None and y > 0 and abs(x - y) / y <= tolerance


def comparable(a: Race, b: Race) -> bool:
    if a.id == b.id or a.race_type != b.race_type:
        return False
    if a.race_category_id is not None or b.race_category_id is not None:
        return a.race_category_id == b.race_category_id
    if ClassificationSource.MANUAL in (a.classification_source, b.classification_source):
        return False
    da, db_ = _distances(a), _distances(b)
    return all(_close(da[s], db_[s], UNCLASSIFIED_MATCH) for s in _sports(a))


# --- checkpoints ------------------------------------------------------------------------


def _elapsed_at(series: list[dict[str, Any]], key: str, value: float) -> float | None:
    """Interpolate elapsed seconds at a distance or progress value."""
    xs = [p[key] for p in series]
    if not xs or xs[-1] is None or value > xs[-1] or value < xs[0]:
        return None
    i = bisect.bisect_left(xs, value)
    if xs[i] == value or i == 0:
        return series[i]["elapsed_s"]
    x0, x1 = xs[i - 1], xs[i]
    t0, t1 = series[i - 1]["elapsed_s"], series[i]["elapsed_s"]
    return t0 + (value - x0) / (x1 - x0) * (t1 - t0) if x1 > x0 else t0


def _lap_step(activity: RecordedActivity | None) -> float | None:
    """The auto-lap length (5 km bike, 1 km run): the median of the full laps."""
    if activity is None:
        return None
    full = [lap.distance_m for lap in activity.laps if not lap.is_partial and lap.distance_m]
    return statistics.median(full) if len(full) >= 2 else None


def _km(metres: float) -> str:
    return f"{metres / 1000:g}k"


def _intra(
    segment: Segment, a: Race, b: Race, times: dict[str, dict[Segment, int | None]]
) -> tuple[list[dict[str, Any]], bool]:
    """Checkpoints inside one sport segment, as (fraction of reference segment, label,
    offset_a, offset_b). Returns them plus whether they are normalised."""
    pa, pb = a.primary_activities().get(segment), b.primary_activities().get(segment)
    step_a, step_b = _lap_step(pa), _lap_step(pb)
    if not (pa and pb and pa.progress_series and pb.progress_series and step_a and step_b):
        return [], False
    da, db_ = pa.distance_m, pb.distance_m
    ta, tb = times["a"][segment], times["b"][segment]
    if not (da and db_ and ta and tb):
        return [], False

    def scaled(activity: RecordedActivity, key: str, value: float, split: int) -> float | None:
        t = _elapsed_at(activity.progress_series or [], key, value)
        return (
            t * split / activity.elapsed_time if t is not None and activity.elapsed_time else None
        )

    out = []
    if _close(db_, da, LIKE_FOR_LIKE):
        k = 1
        while k * step_a < min(da, db_) - step_a / 4:
            d = k * step_a
            out.append(
                {
                    "fraction": d / da,
                    "label": _km(d),
                    "a": scaled(pa, "distance_m", d, ta),
                    "b": scaled(pb, "distance_m", d, tb),
                }
            )
            k += 1
        return out, False
    n = max(2, round(da / step_a))
    for k in range(1, n):
        f = k / n
        out.append(
            {
                "fraction": f,
                "label": _km(round(f * da, -2)),
                "a": scaled(pa, "progress", f, ta),
                "b": scaled(pb, "progress", f, tb),
            }
        )
    return out, True


def checkpoints(a: Race, b: Race) -> dict[str, Any]:
    times = {"a": segment_times(a), "b": segment_times(b)}
    dist = _distances(a)
    segments = a.segments()  # comparable races share a type
    known = [dist[s] for s in _sports(a)]
    if all(known):
        sport_total = sum(known)  # type: ignore[arg-type]
        widths = {
            s: dist[s] if s in SPORT_SEGMENTS else TRANSITION_WIDTH * sport_total for s in segments
        }
    else:  # official splits only: nominal proportions, still honest about order
        nominal = {
            Segment.SWIM: 1.0,
            Segment.T1: 0.1,
            Segment.BIKE: 4.0,
            Segment.T2: 0.1,
            Segment.RUN: 2.0,
        }
        widths = {s: nominal[s] for s in segments}
    scale = sum(widths.values())  # type: ignore[arg-type]

    points = [
        {"x": 0.0, "label": "Start", "boundary": True, "segment": None, "a": 0, "b": 0, "gap_s": 0}
    ]
    x = 0.0
    cum = {"a": 0, "b": 0}
    normalised = False
    coarse = True
    for s in segments:
        width = widths[s] / scale  # type: ignore[operator]
        if s in SPORT_SEGMENTS:
            intra, norm = _intra(s, a, b, times)
            normalised |= norm
            coarse &= not intra
            for c in intra:
                ta = cum["a"] + c["a"] if cum["a"] is not None and c["a"] is not None else None
                tb = cum["b"] + c["b"] if cum["b"] is not None and c["b"] is not None else None
                points.append(
                    {
                        "x": round(x + c["fraction"] * width, 5),
                        "label": c["label"],
                        "boundary": False,
                        "segment": s,
                        "a": _r(ta),
                        "b": _r(tb),
                        "gap_s": _r(tb - ta) if ta is not None and tb is not None else None,
                    }
                )
        x += width
        for side in ("a", "b"):
            t = times[side][s]
            cum[side] = cum[side] + t if cum[side] is not None and t is not None else None
        points.append(
            {
                "x": round(x, 5),
                "label": BOUNDARY_LABELS[s],
                "boundary": True,
                "segment": s,
                "a": cum["a"],
                "b": cum["b"],
                "gap_s": cum["b"] - cum["a"]
                if cum["a"] is not None and cum["b"] is not None
                else None,
            }
        )
    return {"points": points, "normalised": normalised, "coarse": coarse, "times": times}


def _r(value: float | None) -> int | None:
    return round(value) if value is not None else None


# --- conditions and fueling -------------------------------------------------------------


def conditions(a: Race, b: Race, *, owner: bool) -> list[dict[str, Any]]:
    """Only the settings that differ; material differences flagged."""
    out: list[dict[str, Any]] = []
    da, db_ = _distances(a), _distances(b)
    for s in SPORT_SEGMENTS:
        if da[s] and db_[s] and round(da[s]) != round(db_[s]):  # type: ignore[arg-type]
            pct = (db_[s] - da[s]) / da[s] * 100  # type: ignore[operator]
            out.append(
                {
                    "field": f"{s}_distance_m",
                    "a": da[s],
                    "b": db_[s],
                    "delta_pct": round(pct, 1),
                    "material": abs(pct) >= LIKE_FOR_LIKE * 100,
                }
            )

    def elevation(race: Race) -> float | None:
        values = [
            p.elevation_gain_m
            for s, p in race.primary_activities().items()
            if s in SPORT_SEGMENTS and p.elevation_gain_m is not None
        ]
        return sum(values) if values else None

    for name, va, vb, material in (
        ("elevation_gain_m", elevation(a), elevation(b), 50),
        ("temp_c", a.weather_temp_c, b.weather_temp_c, 5),
        ("humidity_pct", a.weather_humidity_pct, b.weather_humidity_pct, 15),
    ):
        if va is not None and vb is not None and round(va) != round(vb):
            out.append(
                {
                    "field": name,
                    "a": va,
                    "b": vb,
                    "delta": round(vb - va, 1),
                    "material": abs(vb - va) >= material,
                }
            )
    if (
        a.weather_conditions
        and b.weather_conditions
        and a.weather_conditions != b.weather_conditions
    ):
        out.append(
            {
                "field": "conditions",
                "a": a.weather_conditions,
                "b": b.weather_conditions,
                "material": False,
            }
        )

    pa, pb = a.primary_activities(), b.primary_activities()
    for s in SEGMENTS:
        ga = sorted(link.gear.name for link in pa[s].gear_links) if s in pa else []
        gb = sorted(link.gear.name for link in pb[s].gear_links) if s in pb else []
        if ga and gb and ga != gb:
            out.append({"field": f"{s}_gear", "a": ga, "b": gb, "material": True})
        if owner and s in pa and s in pb and pa[s].device_key != pb[s].device_key:
            out.append(
                {
                    "field": f"{s}_device",
                    "a": pa[s].device_key,
                    "b": pb[s].device_key,
                    "material": True,
                }
            )
    return out


def fueling(a: Race, b: Race, times: dict[str, dict[Segment, int | None]]) -> list[dict[str, Any]]:
    fa = {f.segment: f for f in a.fueling}
    fb = {f.segment: f for f in b.fueling}
    out = []
    for s in SEGMENTS:
        if s not in fa and s not in fb:
            continue

        def side(row, seconds):
            if row is None:
                return None
            gph = row.carbs_g * 3600 / seconds if row.carbs_g is not None and seconds else None
            return {
                "carbs_g": row.carbs_g,
                "g_per_h": round(gph) if gph is not None else None,
                "products": row.products,
            }

        sa, sb = side(fa.get(s), times["a"][s]), side(fb.get(s), times["b"][s])
        names_a = {p["name"] for p in (sa or {}).get("products", [])}
        names_b = {p["name"] for p in (sb or {}).get("products", [])}
        ca, cb = (sa or {}).get("carbs_g"), (sb or {}).get("carbs_g")
        out.append(
            {
                "segment": s,
                "a": sa,
                "b": sb,
                "carbs_delta_g": cb - ca if ca is not None and cb is not None else None,
                "only_a": sorted(names_a - names_b),
                "only_b": sorted(names_b - names_a),
            }
        )
    return out


def compare(a: Race, b: Race, *, owner: bool) -> dict[str, Any]:
    if not comparable(a, b):
        raise NotComparable("These races are in different categories and can't be compared.")
    chart = checkpoints(a, b)
    times = chart.pop("times")
    deltas = {
        s: times["b"][s] - times["a"][s]
        if times["a"][s] is not None and times["b"][s] is not None
        else None
        for s in SEGMENTS
    }
    return {
        "checkpoints": chart["points"],
        "normalised": chart["normalised"],
        "coarse": chart["coarse"],
        "segment_times": times,
        "deltas": deltas,
        "conditions": conditions(a, b, owner=owner),
        "fueling": fueling(a, b, times),
    }
