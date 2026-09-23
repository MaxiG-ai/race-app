"""Resolving an official result's total time.

`OfficialResult.total_time` is NOT NULL: a result without a total is not a result. But
the user should never have to reach for a calculator — entering the five splits is
enough, and the total follows from them. When it cannot be derived, the save is refused
with a message naming exactly what is missing, rather than storing a wrong or zero total.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The five legs, in course order. Their sum is the total.
SPLIT_FIELDS: tuple[str, ...] = ("swim_time", "t1_time", "bike_time", "t2_time", "run_time")

#: Human labels for the prompt, so the message reads like the form does.
SPLIT_LABELS: dict[str, str] = {
    "swim_time": "swim",
    "t1_time": "T1",
    "bike_time": "bike",
    "t2_time": "T2",
    "run_time": "run",
}


class TotalTimeUnresolvable(ValueError):
    """No total was given and the splits are too incomplete to derive one."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        labels = ", ".join(SPLIT_LABELS[f] for f in missing)
        super().__init__(
            "Enter the total time, or fill in every split so it can be calculated. "
            f"Still missing: {labels}."
        )


@dataclass(frozen=True, slots=True)
class ResolvedTotal:
    total_time: int
    #: True when the total came from summing the splits rather than from the user.
    derived: bool


def missing_splits(splits: dict[str, int | None]) -> list[str]:
    return [f for f in SPLIT_FIELDS if splits.get(f) is None]


def resolve_total_time(
    total_time: int | None, splits: dict[str, int | None]
) -> ResolvedTotal:
    """Return the total to store, deriving it from the splits when it wasn't given.

    A total the user typed always wins: they may be copying the organizer's figure,
    which can differ from the sum by a second or two of chip-timing rounding, and
    silently replacing it would overwrite the more authoritative number.
    """
    if total_time is not None:
        return ResolvedTotal(total_time=total_time, derived=False)

    missing = missing_splits(splits)
    if missing:
        raise TotalTimeUnresolvable(missing)

    return ResolvedTotal(
        total_time=sum(int(splits[f]) for f in SPLIT_FIELDS),  # type: ignore[arg-type]
        derived=True,
    )
