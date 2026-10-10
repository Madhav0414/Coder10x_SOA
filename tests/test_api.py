"""The HTTP layer the central station runs on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prahari.api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Prahari" in r.text
    assert "/static/js/app.js" in r.text


def test_ward_has_eight_ranked_beds_with_forecasts(client):
    d = client.get("/api/ward", params={"reveal": 4}).json()
    assert len(d["beds"]) == 8
    for b in d["beds"]:
        assert b["obs"], "every bed has charted observations"
        assert b["forecast"]["t"][0] == pytest.approx(d["clock"]), "forecast starts at the round"
        assert b["quadrant"]["code"] in "ABLC"
        assert 0.0 <= b["confidence"] <= 1.0
    alerts = [b["alert"] for b in d["beds"]]
    assert alerts == sorted(alerts, reverse=True), "alerting beds are ranked first"


def test_patient_detail_carries_the_twin(client):
    bed = client.get("/api/ward", params={"reveal": 4}).json()["beds"][0]["bed"]
    d = client.get(f"/api/patient/{bed}", params={"reveal": 4}).json()
    assert d["twin"]["ci"] > 0 and d["twin"]["pcwp"] > 0
    assert d["options"], "counterfactual options are simulated"
    assert len(d["band"]["t"]) == len(d["band"]["ci"]["lo"])
    assert d["record"]["cyp2d6"] in {"poor", "intermediate", "normal", "ultrarapid"}


def test_whatif_forks_at_the_ward_clock(client):
    ward = client.get("/api/ward", params={"reveal": 4}).json()
    bed = next(b for b in ward["beds"] if b["quadrant"]["code"] in "BC")
    d = client.get(f"/api/whatif/{bed['bed']}",
                   params={"agent": "furosemide", "delay": 3, "now": 17.5, "reveal": 4}).json()
    assert d["now"] == pytest.approx(17.5)
    assert d["act_now"]["t"][0] == pytest.approx(17.5)
    assert d["given"] == pytest.approx(20.5)


def test_unknown_agent_is_rejected(client):
    assert client.get("/api/whatif/1", params={"agent": "aspirin"}).status_code == 400


def test_evidence_compares_twin_with_truth(client):
    d = client.get("/api/evidence", params={"reveal": 4}).json()
    assert len(d["rows"]) == 8
    assert all(r["twinQ"] in "ABLC" for r in d["rows"])


def test_handover_is_plain_text(client):
    r = client.get("/api/handover", params={"reveal": 4})
    assert r.status_code == 200
    assert "PRAHARI SHIFT HANDOVER" in r.text


def test_discharge_forecasts_days_and_labels_every_channel(client):
    r = client.get("/api/discharge/6", params={"reveal": 4, "course": "decompensating"}).json()
    assert r["estimate"]["band"] in ("low", "moderate", "high")
    assert set(r["plans"]) == {"no_change", "restrict", "diuretic"}
    assert set(r["source"]["channels"]) == {"weight", "rhr", "hrv", "awake", "sleep", "steps"}
    assert 8 <= r["today"] <= r["totalDays"] == len(r["days"])
    assert r["heart"]["limitL"] > 1.0
    # a later "today" sees more of the slide
    later = client.get("/api/discharge/6", params={"reveal": 4, "course": "decompensating",
                                                   "upto": r["totalDays"]}).json()
    assert later["estimate"]["fluidL"] >= r["estimate"]["fluidL"]


def test_a_saved_round_answers_like_a_live_fit(client, tmp_path, monkeypatch):
    """Deploys fit the rounds during the build and keep them on disk; a visitor
    must see the same ward from a saved round as from a live fit."""
    from prahari import api

    live = client.get("/api/ward", params={"reveal": 2}).json()
    monkeypatch.setattr(api, "ROUNDS", tmp_path)
    assert api.save_round(7, 4.0, 2, 12.0)
    api._fitted_round.cache_clear()

    def no_fitting(*a, **k):
        raise AssertionError("the saved round should have been used")
    monkeypatch.setattr(api, "fit_ward_at", no_fitting)
    saved = client.get("/api/ward", params={"reveal": 2}).json()
    api._fitted_round.cache_clear()
    assert saved == live
