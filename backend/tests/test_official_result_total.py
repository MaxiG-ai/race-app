"""Total time is required, but never something the user has to work out themselves.

`OfficialResult.total_time` stays NOT NULL — a result without a total is not a result.
Entering the five splits is enough; anything less is refused with a prompt naming the
gaps, rather than stored as a zero or a guess.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.official_result import (
    TotalTimeUnresolvable,
    missing_splits,
    resolve_total_time,
)

FULL_SPLITS = {
    "swim_time": 1739,
    "t1_time": 216,
    "bike_time": 4388,
    "t2_time": 121,
    "run_time": 3363,
}
FULL_SUM = 1739 + 216 + 4388 + 121 + 3363  # 9827


# --- the unit rules -------------------------------------------------------------


def test_total_is_summed_from_a_complete_set_of_splits():
    resolved = resolve_total_time(None, FULL_SPLITS)
    assert resolved.total_time == FULL_SUM
    assert resolved.derived is True


def test_a_typed_total_is_kept_even_when_the_splits_disagree():
    """Chip timing rounds, and organizers sometimes fold transitions into adjacent legs.
    The figure the user copied off the results sheet is the authoritative one."""
    resolved = resolve_total_time(FULL_SUM + 3, FULL_SPLITS)
    assert resolved.total_time == FULL_SUM + 3
    assert resolved.derived is False


def test_a_typed_total_needs_no_splits_at_all():
    resolved = resolve_total_time(9827, dict.fromkeys(FULL_SPLITS, None))
    assert resolved.total_time == 9827
    assert resolved.derived is False


def test_one_missing_split_blocks_derivation_and_names_it():
    splits = FULL_SPLITS | {"t1_time": None}
    with pytest.raises(TotalTimeUnresolvable) as excinfo:
        resolve_total_time(None, splits)
    assert excinfo.value.missing == ["t1_time"]
    assert "T1" in str(excinfo.value)


def test_the_prompt_lists_every_gap_not_just_the_first():
    splits = FULL_SPLITS | {"swim_time": None, "run_time": None}
    with pytest.raises(TotalTimeUnresolvable) as excinfo:
        resolve_total_time(None, splits)
    assert excinfo.value.missing == ["swim_time", "run_time"]
    message = str(excinfo.value)
    assert "swim" in message and "run" in message


def test_nothing_entered_at_all_asks_for_the_total():
    with pytest.raises(TotalTimeUnresolvable):
        resolve_total_time(None, dict.fromkeys(FULL_SPLITS, None))


def test_a_zero_split_counts_as_entered():
    """A 0-second T2 is a real value — a race where the organizer didn't time it
    separately. Only `None` means 'not entered'."""
    splits = FULL_SPLITS | {"t2_time": 0}
    assert missing_splits(splits) == []
    assert resolve_total_time(None, splits).total_time == FULL_SUM - 121


# --- through the API ---------------------------------------------------------------


@pytest.fixture
def race_id(client: TestClient, db: Session) -> int:
    """A logged-in session with one bare race, ready for an official result."""
    from tests import factories

    user = factories.make_user(db)
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": factories.DEFAULT_PASSWORD},
    )
    assert response.status_code == 200
    created = client.post(
        "/api/races",
        json={"name": "Total time race", "date": "2026-08-09", "race_type": "olympic"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_posting_only_splits_stores_the_computed_total(client: TestClient, race_id: int):
    response = client.put(
        f"/api/races/{race_id}/official-result",
        json=FULL_SPLITS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total_time"] == FULL_SUM


def test_posting_incomplete_splits_is_refused_with_a_usable_prompt(
    client: TestClient, race_id: int
):
    response = client.put(
        f"/api/races/{race_id}/official-result",
        json={"swim_time": 1739, "bike_time": 4388},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["missing_splits"] == ["t1_time", "t2_time", "run_time"]
    assert "Enter the total time" in detail["message"]


def test_refusing_the_save_leaves_no_partial_row_behind(client: TestClient, race_id: int):
    client.put(
        f"/api/races/{race_id}/official-result",
        json={"swim_time": 1739},
    )
    detail = client.get(f"/api/races/{race_id}").json()
    assert detail["official_result"] is None


def test_a_total_alone_is_accepted(client: TestClient, race_id: int):
    """The common case on the day: you know you finished in 2:43:47 and will fill in the
    splits later."""
    response = client.put(
        f"/api/races/{race_id}/official-result",
        json={"total_time": 9827},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_time"] == 9827
    assert body["swim_time"] is None


def test_filling_in_splits_later_does_not_overwrite_the_recorded_total(
    client: TestClient, race_id: int
):
    client.put(
        f"/api/races/{race_id}/official-result",
        json={"total_time": 9900},
    )
    response = client.put(
        f"/api/races/{race_id}/official-result",
        json={"total_time": 9900, **FULL_SPLITS},
    )
    assert response.status_code == 200
    assert response.json()["total_time"] == 9900


def test_dropping_the_total_on_a_later_edit_recomputes_from_splits(
    client: TestClient, race_id: int
):
    client.put(
        f"/api/races/{race_id}/official-result",
        json={"total_time": 9900},
    )
    response = client.put(
        f"/api/races/{race_id}/official-result",
        json=FULL_SPLITS,
    )
    assert response.status_code == 200
    assert response.json()["total_time"] == FULL_SUM
