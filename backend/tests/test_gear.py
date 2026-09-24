"""docs/features/gear.md#acceptance."""

from tests.conftest import upload


def _race_with_edge(owner):
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    edge = upload(owner, "edge_single_bike.fit")[0]["upload_id"]
    owner.post(f"/api/races/{race_id}/attach", json={"upload_ids": [edge]})
    return race_id


def test_edge_sensors_are_unknown_devices_and_mapping_backfills(owner):
    race_id = _race_with_edge(owner)
    unknown = owner.get("/api/gear/unknown-devices").json()
    assert {(d["manufacturer"], d["serial_number"]) for d in unknown} == {
        ("favero_electronics", "800800800"),
        ("shimano", "900900900"),
        ("polar_electro", "111222333"),
    }
    bike = owner.post("/api/gear", json={"kind": "bike", "name": "Speedmax"}).json()
    owner.post(
        f"/api/gear/{bike['id']}/devices",
        json={"manufacturer": "favero_electronics", "serial_number": "800800800"},
    )
    recordings = owner.get(f"/api/races/{race_id}").json()["recordings"]
    edge_bike = next(r for r in recordings if r["segment"] == "bike" and r["sensors"])
    assert [g["name"] for g in edge_bike["gear"]] == ["Speedmax"]
    assert len(owner.get("/api/gear/unknown-devices").json()) == 2


def test_distance_counts_primary_recordings_only(owner):
    race_id = _race_with_edge(owner)
    bike = owner.post(
        "/api/gear", json={"kind": "bike", "name": "Speedmax", "distance_offset_m": 1000}
    ).json()
    for r in owner.get(f"/api/races/{race_id}").json()["recordings"]:
        if r["segment"] == "bike":
            owner.patch(f"/api/activities/{r['id']}", json={"gear_ids": [bike["id"]]})
    inventory = owner.get("/api/gear").json()
    assert inventory[0]["distance_m"] == 1000 + 33746  # the Apple bike is primary


def test_manual_assignment_is_never_overwritten(owner):
    race_id = _race_with_edge(owner)
    shoes = owner.post("/api/gear", json={"kind": "other", "name": "Manual pick"}).json()
    edge_bike = next(
        r
        for r in owner.get(f"/api/races/{race_id}").json()["recordings"]
        if r["segment"] == "bike" and r["sensors"]
    )
    owner.patch(f"/api/activities/{edge_bike['id']}", json={"gear_ids": [shoes["id"]]})
    bike = owner.post("/api/gear", json={"kind": "bike", "name": "Speedmax"}).json()
    owner.post(
        f"/api/gear/{bike['id']}/devices",
        json={"manufacturer": "shimano", "serial_number": "900900900"},
    )
    after = next(
        r
        for r in owner.get(f"/api/races/{race_id}").json()["recordings"]
        if r["id"] == edge_bike["id"]
    )
    assert [g["name"] for g in after["gear"]] == ["Manual pick"]


def test_bike_gear_is_not_auto_attached_to_the_run(owner):
    bike = owner.post("/api/gear", json={"kind": "bike", "name": "Speedmax"}).json()
    owner.post(
        f"/api/gear/{bike['id']}/devices",
        json={"manufacturer": "favero_electronics", "serial_number": "800800800"},
    )
    upload(owner, "edge_single_bike.fit")
    body = owner.post("/api/races/group", json={"upload_ids": [1], "segments": {"1": "run"}})
    assert body.json()["segments"][0]["gear"] == []


def test_last_used_shoes(owner):
    assert owner.get("/api/gear/last-used-shoes").json() is None
    race_id = upload(owner, "apple_multisport.fit")[0]["race_id"]
    shoes = owner.post("/api/gear", json={"kind": "shoes", "name": "Vaporfly"}).json()
    run = next(
        s for s in owner.get(f"/api/races/{race_id}").json()["segments"] if s["segment"] == "run"
    )
    owner.patch(f"/api/activities/{run['id']}", json={"gear_ids": [shoes["id"]]})
    assert owner.get("/api/gear/last-used-shoes").json()["name"] == "Vaporfly"


def test_retire_and_duplicate_mapping(owner):
    item = owner.post("/api/gear", json={"kind": "shoes", "name": "Old"}).json()
    assert (
        owner.patch(f"/api/gear/{item['id']}", json={"retired_at": "2026-01-01"}).json()[
            "retired_at"
        ]
        == "2026-01-01"
    )
    body = {"manufacturer": "shimano", "serial_number": "1"}
    assert owner.post(f"/api/gear/{item['id']}/devices", json=body).status_code == 200
    assert owner.post(f"/api/gear/{item['id']}/devices", json=body).status_code == 409
