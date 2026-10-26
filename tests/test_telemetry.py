import json
import re

import numpy as np
import pandas as pd
import pytest

from app import create_app
from datasets import BASE, MISSIONS, DataError, load_config, load_dataset, timestamp
from telemetry import TelemetryStore, display_window, select_extrema, select_states
from test_app import extract_token, fields, post_csv


def page_data(response):
    match = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', response.get_data(as_text=True), re.S)
    return json.loads(match[1])


def request_series(client, response, channel, date, scale="day", scored=False):
    token = extract_token(response, "json") if scored else page_data(response)["chart"]["token"]
    return client.post("/api/series", json=dict(kind="result" if scored else "chart", token=token,
                                               channel=channel, date=date, scale=scale))


@pytest.mark.parametrize("mission", MISSIONS)
def test_three_mission_raw_curves_and_scored_channel_specific_shading(client, mission):
    cfg = load_config(mission, "demo")
    page = client.get("/", query_string=dict(mission=mission, source="demo"))
    assert 'id="raw-visualization"' in page.get_data(as_text=True)
    ds = load_dataset(cfg)
    for scale in ("day", "month", "year"):
        result = request_series(client, page, ds["channels"][0], cfg["start"][:10], scale)
        assert result.status_code == 200
        result = result.get_json()
        assert result["mission"] == mission
        assert result["raw_count"] > 0 and result["segments"]
        assert result["events"]
        assert all(len(point) == 5 for segment in result["segments"] for point in segment)
        assert result["window_start"] == cfg["start"]
        assert result["window_end"] == cfg["end"]
        assert result["scale"] == scale
        assert not result["detections"] and not result["has_scored_predictions"]
    scored = post_csv(client, mission)
    date = cfg["start"][:10]
    if mission == "Mission1":
        channel, expected = "channel_2", [(12, 18)]
    elif mission == "Mission2":
        channel, expected = "channel_1", [(12, 18)]
    else:
        channel, expected = "channel_4", [(30, 30), (82, 88)]
    chart = request_series(client, scored, channel, date, scored=True).get_json()
    assert chart["has_scored_predictions"]
    assert [(d["start"], d["end"]) for d in chart["detections"]] == expected
    if mission == "Mission3":
        assert chart["kind"] == "categorical"
        assert "value_0" in chart["categories"]
        assert chart["detections"][0]["point"]
    else:
        assert chart["kind"] == "numeric"
    # Viewing a curve does not change the signed score snapshot.
    original = client.post("/download/json", data=dict(token=extract_token(scored, "json"))).get_json()
    assert original["config"]["target_channels"] == ds["channels"]
    assert original["config"]["start"] == cfg["start"]


def raw_dataset(tmp_path, index, values, mission="Mission1", version="fixture-v1"):
    folder = tmp_path / mission
    (folder / "channels").mkdir(parents=True, exist_ok=True)
    path = folder / "channels" / "channel_1.zip"
    pd.DataFrame({"channel_1": values}, index=index).to_pickle(path)
    cfg = dict(mission_id=mission, source="real", version=version, paths={"labels": str(folder / "labels.csv")})
    return dict(config=cfg, channels=["channel_1"], window=(timestamp("2024-01-01"), timestamp("2025-01-01")))


def test_extrema_downsampling_keeps_true_samples_and_narrow_spikes():
    values = np.zeros(100000)
    values[12345] = 500
    values[54321] = -300
    picked = select_extrema(values, 100)
    assert 12345 in picked and 54321 in picked
    assert 0 in picked and len(values)-1 in picked
    assert len(picked) <= 100
    assert np.all(np.diff(picked) > 0)


def test_state_downsampling_preserves_brief_intermediate_state():
    values = np.zeros(1000)
    values[40] = 1
    values[700:710] = 2
    picked = select_states(values, 30, np.zeros(len(values), dtype=int))
    assert {39, 40, 41, 699, 700, 709, 710}.issubset(picked)
    assert set(values[picked]) == {0, 1, 2}


def test_long_gaps_nan_and_overlap_union_shading(tmp_path):
    seconds = [0, 1, 2, 3, 4, 1000, 1001, 1002, 1003, 1004]
    index = timestamp("2024-01-01") + pd.to_timedelta(seconds, unit="s")
    ds = raw_dataset(tmp_path, index, [1, 2, np.nan, 3, 4, 5, 6, 7, 8, 9])
    predictions = [dict(channel="channel_1", start="2024-01-01T00:00:00Z", end="2024-01-01T00:16:44Z"),
                   dict(channel="channel_1", start="2024-01-01T00:00:03Z", end="2024-01-01T00:00:04Z")]
    result = TelemetryStore().window(ds, "channel_1", "2024-01-01", "day", predictions)
    assert len(result["segments"]) == 3
    assert result["invalid_count"] == 1
    assert [(p["start"], p["end"]) for p in result["detections"]] == [(0, 1), (3, 4), (1000, 1004)]
    assert not result["downsampled"]


def test_window_limit_sampling_and_empty_data(tmp_path):
    index = pd.date_range("2024-01-01", periods=100000, freq="s", tz="UTC")
    values = np.sin(np.arange(len(index))/100)
    ds = raw_dataset(tmp_path, index, values)
    result = TelemetryStore().window(ds, "channel_1", "2024-01-01", "day", max_points=100)
    assert result["raw_count"] == 86401
    assert result["displayed_count"] <= 100
    assert result["downsampled"]
    original = {stamp.value: value for stamp,value in zip(index, values)}
    for segment in result["segments"]:
        for _, value, time, *_ in segment:
            assert original[timestamp(time).value] == value
    empty = TelemetryStore().window(ds, "channel_1", "2024-02-01", "day")
    assert empty["segments"] == [] and empty["detections"] == []


def test_calendar_scales_utc_leap_month_and_clipping():
    bounds = (timestamp("2024-01-15"), timestamp("2025-03-10"))
    feb = display_window(bounds, "2024-02-15", "month")
    assert feb["start"] == timestamp("2024-02-01")
    assert feb["end"] == timestamp("2024-03-01")
    assert (feb["end"]-feb["start"]).days == 29
    year = display_window(bounds, "2024-06-10", "year")
    assert year["start"] == bounds[0] and year["end"] == timestamp("2025-01-01")
    assert year["requested_date"] == timestamp("2024-06-10")
    assert display_window(bounds, "2024-01-20", "month")["previous_date"] is None


def test_cache_isolated_by_mission_version_and_file_changes(tmp_path):
    index = pd.date_range("2024-01-01", periods=2, freq="s", tz="UTC")
    first = raw_dataset(tmp_path, index, [1, 2], "Mission1")
    second = raw_dataset(tmp_path, index, [90, 91], "Mission2")
    store = TelemetryStore()
    assert store.series(first, "channel_1")["values"][0] == 1
    assert store.series(second, "channel_1")["values"][0] == 90
    assert store.series(first, "channel_1")["values"][0] == 1
    first["config"]["version"] = "fixture-v2"
    assert store.series(first, "channel_1")["values"][0] == 1
    raw_dataset(tmp_path, index, [30, 31], "Mission1")
    assert store.series(first, "channel_1")["values"][0] == 30


def test_bad_scope_and_missing_raw_file_are_readable_errors(client, tmp_path):
    ds = load_dataset(load_config("Mission1", "demo"))
    with pytest.raises(DataError, match="目标通道"):
        TelemetryStore().window(ds, "unknown", "2024-01-01", "day")
    with pytest.raises(DataError, match="时间尺度"):
        TelemetryStore().window(ds, "channel_1", "2024-01-01", "century")
    with pytest.raises(DataError, match="超出"):
        TelemetryStore().window(ds, "channel_1", "1999-01-01", "day")
    ds["config"]["source"] = "real"
    ds["config"]["paths"]["labels"] = str(tmp_path / "labels.csv")
    with pytest.raises(DataError, match="无法读取"):
        TelemetryStore().window(ds, "channel_1", "2024-01-01", "day")
    assert client.post("/api/series", json=dict(token="forged", channel="channel_1")).status_code == 400


def test_stale_chart_context_and_unknown_channel_rejected(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    source = json.loads((BASE / "configs" / "Mission1.json").read_text(encoding="utf-8"))
    file = config_dir / "Mission1.json"
    file.write_text(json.dumps(source), encoding="utf-8")
    client = create_app(dict(TESTING=True, CONFIG_DIR=config_dir)).test_client()
    page = client.get("/?mission=Mission1&source=demo")
    rejected = request_series(client, page, "channel_3", "2024-01-01")
    assert rejected.status_code == 400 and "目标通道" in rejected.get_json()["error"]
    source["sources"]["demo"]["end"] = "2024-01-01T00:01:30Z"
    file.write_text(json.dumps(source), encoding="utf-8")
    stale = request_series(client, page, "channel_1", "2024-01-01")
    assert stale.status_code == 400 and "旧图表已失效" in stale.get_json()["error"]


def test_event_identity_overlap_fragments_and_stable_colors(tmp_path):
    index = pd.date_range("2024-01-01", periods=60, freq="s", tz="UTC")
    ds = raw_dataset(tmp_path, index, np.arange(60))
    at = lambda second: index[second]
    ds["event_ids"] = ["A", "B", "other_channel"]
    ds["labels"] = [dict(id="A", channel="channel_1", start=at(5), end=at(10), category="Anomaly"),
                    dict(id="A", channel="channel_1", start=at(20), end=at(25), category="Anomaly"),
                    dict(id="B", channel="channel_1", start=at(8), end=at(12), category="Rare Event"),
                    dict(id="other_channel", channel="channel_2", start=at(15), end=at(18), category="Anomaly")]
    store = TelemetryStore()
    result = store.window(ds, "channel_1", "2024-01-01", "day")
    events = {event["id"]: event for event in result["events"]}
    assert set(events) == {"A", "B"}
    assert events["A"]["color"] != events["B"]["color"]
    assert [(i["start"], i["end"]) for i in events["A"]["intervals"]] == [(5, 10), (20, 25)]
    points = {point[0]: point for segment in result["segments"] for point in segment}
    assert points[9][4] == ["A", "B"]
    assert points[10][4] == ["A", "B"]  # closed ground-truth endpoints
    assert points[11][4] == ["B"]
    assert points[15][4] == []
    assert points[20][3] == 20  # original channel-local sample index, not display rank
    # A different window/category must not recolor B or pretend its rare label vanished.
    ds["window"] = (at(11), at(18))
    narrower = store.window(ds, "channel_1", "2024-01-01", "day", category_mode="anomaly")
    assert [event["id"] for event in narrower["events"]] == ["B"]
    assert narrower["events"][0]["color"] == events["B"]["color"]
    assert not narrower["events"][0]["selected"]
    first_point = narrower["segments"][0][0]
    assert first_point[0] == 0 and first_point[3] == 11 and first_point[4] == ["B"]


def test_event_colors_use_full_mission_catalogue_across_channels(demo):
    ds = demo("Mission1")
    assert ds["event_ids"] == ["id_1", "id_2"]
    store = TelemetryStore()
    first = store.window(ds, "channel_1", "2024-01-01", "day")
    second = store.window(ds, "channel_2", "2024-01-01", "day")
    assert next(e for e in first["events"] if e["id"] == "id_1")["color"] == next(e for e in second["events"] if e["id"] == "id_1")["color"]


def test_point_detection_and_true_point_keep_zero_duration(demo):
    ds = demo("Mission3")
    pred = [dict(channel="channel_4", start="2024-03-01T00:00:30Z", end="2024-03-01T00:00:30Z")]
    result = TelemetryStore().window(ds, "channel_4", "2024-03-01", "day", pred)
    assert result["detections"][0]["start"] == result["detections"][0]["end"] == 30
    assert result["detections"][0]["point"] and result["detections"][0]["right_closed"]
    point_event = next(e for e in result["events"] if e["id"] == "point")
    assert point_event["intervals"][0]["point"]
    sample = next(p for s in result["segments"] for p in s if p[0] == 30)
    assert sample[4] == ["point"]
