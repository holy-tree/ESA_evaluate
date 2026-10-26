import numpy as np
import pandas as pd
import pytest

from chart_data import build_view, display_cadence, prediction_checkpoints, smooth_buckets
from datasets import MISSIONS, load_config, load_dataset, timestamp
from telemetry import TelemetryStore
from test_app import exported, fields
from test_telemetry import page_data, raw_dataset


def view_request(client, page, channels, start, end, **extras):
    payload = dict(kind="chart", token=page_data(page)["chart"]["token"], channels=channels,
                   view_start=start, view_end=end, pixel_width=900, display_mode="auto")
    payload.update(extras)
    return client.post("/api/series", json=payload)


@pytest.mark.parametrize("mission", MISSIONS)
def test_multi_channel_continuous_api_and_categories(client, mission):
    cfg = load_config(mission, "demo")
    ds = load_dataset(cfg)
    page = client.get("/", query_string=dict(mission=mission, source="demo"))
    response = view_request(client, page, ds["channels"], cfg["start"], cfg["end"])
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert [r["channel"] for r in result["channels"]] == ds["channels"]
    assert len({r["color"] for r in result["channels"]}) == len(ds["channels"])
    assert all(r["raw_count"] > 0 for r in result["channels"])
    assert result["navigation_range"] == [cfg["start"], cfg["end"]]
    assert result["evaluation_range"] == [cfg["start"], cfg["end"]]
    if mission == "Mission3":
        assert all(r["kind"] == "categorical" and r["mode"] == "states" for r in result["channels"])


def test_pixel_aggregation_preserves_extrema_without_zigzagging(tmp_path):
    index = pd.date_range("2024-01-01", periods=10000, freq="s", tz="UTC")
    values = np.sin(np.arange(10000))
    values[5050] = 999
    ds = raw_dataset(tmp_path, index, values)
    result = build_view(TelemetryStore(), ds, ["channel_1"], index[0].isoformat(), index[-1].isoformat(), pixel_width=400)
    c = result["channels"][0]
    assert c["mode"] == "aggregate"
    assert sum(b["count"] for b in c["bins"]) == len(values)
    assert max(b["max"] for b in c["bins"]) == 999
    assert max(abs(p[1]) for s in c["trend_segments"] for p in s) < 1
    assert sum(map(len,c["trend_segments"])) < 200
    assert all(p[1] == c["bins"][p[2]]["median"] for s in c["trend_segments"] for p in s)
    assert any(p[1] == 999 for s in c["segments"] for p in s)


def test_smoothing_uses_time_weights_and_never_crosses_breaks():
    bins = [dict(offset=0, mean=10, median=10, count=2, block=0),
            dict(offset=1, mean=40, median=40, count=1, block=0),
            dict(offset=1.1, mean=1000, median=1000, count=50, block=1)]
    moving = smooth_buckets(bins, "moving", 4)
    assert moving.tolist() == [20, 20, 1000]
    kernel = smooth_buckets(bins, "kernel", 1)
    expected = (2*10+np.exp(-.5)*40)/(2+np.exp(-.5))
    assert kernel[0] == pytest.approx(expected)
    assert kernel[2] == 1000


def test_aggregate_and_smooth_split_nan_and_long_sampling_intervals(tmp_path):
    index = timestamp("2024-01-01") + pd.to_timedelta([0, 1, 2, 3, 1000, 1001], unit="s")
    ds = raw_dataset(tmp_path, index, [1, 2, np.nan, 3, 90, 100])
    result = build_view(TelemetryStore(), ds, ["channel_1"], index[0].isoformat(), index[-1].isoformat(), mode="kernel", bandwidth=9999)
    c = result["channels"][0]
    assert len(c["trend_segments"]) == 3
    assert sum(b["count"] for b in c["bins"]) == 5
    assert c["invalid_count"] == 1
    assert max(p[1] for p in c["trend_segments"][0]) < 3
    assert min(p[1] for p in c["trend_segments"][-1]) >= 90


def test_common_slow_sampling_regime_is_not_split_into_individual_points():
    steps = np.r_[np.full(5000, 375_000_000, dtype=np.int64),
                  np.full(2000, 90_000_000_000, dtype=np.int64),
                  [2_000_000_000_000]]
    times = np.r_[0, np.cumsum(steps)]
    gap, _ = display_cadence(times, 3_750_000_000)
    assert gap >= 900_000_000_000
    assert gap < steps[-1]
    assert int((np.diff(times) > gap).sum()) == 1


def test_state_series_are_not_numerically_smoothed(demo):
    ds = demo("Mission3")
    result = build_view(TelemetryStore(), ds, ds["channels"], ds["config"]["start"], ds["config"]["end"], mode="kernel", bandwidth=100)
    assert all(c["mode"] == "states" and not c["bins"] and not c["trend_segments"] for c in result["channels"])
    assert all(p[1].is_integer() for c in result["channels"] for s in c["segments"] for p in s)


def test_checkpoint_union_keeps_prediction_sources_and_point_identity():
    pred = [dict(channel="a", start="2024-01-01T00:00:10Z", end="2024-01-01T00:00:20Z"),
            dict(channel="b", start="2024-01-01T00:00:15Z", end="2024-01-01T00:00:30Z"),
            dict(channel="a", start="2024-01-01T00:00:50Z", end="2024-01-01T00:00:50Z")]
    checkpoints = prediction_checkpoints(pred, ["a", "b"])
    assert len(checkpoints) == 2
    assert checkpoints[0]["channels"] == ["a", "b"]
    assert checkpoints[0]["prediction_indices"] == [0, 1]
    assert checkpoints[0]["end"] == "2024-01-01T00:00:30Z"
    assert checkpoints[1]["point"]
    assert prediction_checkpoints(pred, ["b"])[0]["prediction_indices"] == [1]


def test_view_outside_evaluation_keeps_score_unchanged(client):
    response = client.post("/", data=dict(fields("Mission3", "manual"), channel="channel_1",
        starttime="2024-03-01T00:00:12Z", endtime="2024-03-01T00:00:18Z",
        range_start="2024-03-01T00:00:10Z", range_end="2024-03-01T00:00:50Z", confirm_range="yes"))
    before = exported(client, response)
    from test_app import extract_token
    chart = client.post("/api/series", json=dict(kind="result", token=extract_token(response,"json"), channels=["channel_1", "channel_4"],
        view_start="2024-03-01T00:00:00Z", view_end="2024-03-01T00:01:40Z", display_mode="aggregate", include_checkpoints=True))
    assert chart.status_code == 200, chart.get_json()
    chart = chart.get_json()
    assert chart["evaluation_range"] == ["2024-03-01T00:00:10Z", "2024-03-01T00:00:50Z"]
    assert chart["navigation_range"] == ["2024-03-01T00:00:00Z", "2024-03-01T00:01:40Z"]
    assert any(e["id"] == "invalid" for c in chart["channels"] for e in c["events"])
    assert len(chart["checkpoints"]) == 1
    assert exported(client,response) == before


def test_byte_bounded_cache_evicts_without_cross_mission_values(tmp_path, monkeypatch):
    index = pd.date_range("2024-01-01", periods=3, freq="s", tz="UTC")
    first = raw_dataset(tmp_path,index,[1.,2.,3.],"Mission1")
    second = raw_dataset(tmp_path,index,[91.,92.,93.],"Mission2")
    store = TelemetryStore(max_bytes=60)
    calls = []
    original = store._read
    def read(key):
        calls.append(key)
        return original(key)
    monkeypatch.setattr(store,"_read",read)
    assert store.series(first,"channel_1")["values"][0] == 1
    assert store.series(second,"channel_1")["values"][0] == 91
    assert store.series(first,"channel_1")["values"][0] == 1
    assert len(calls) == 3
    assert store.cached_bytes <= 60


@pytest.mark.parametrize("change", [dict(channels=[]), dict(channels=["unknown"]), dict(display_mode="bad"),
    dict(bandwidth_seconds=0), dict(view_start="2030-01-01"), dict(view_end="2023-01-01")])
def test_bad_continuous_requests_are_rejected(client, change):
    page = client.get("/?mission=Mission1&source=demo")
    request = dict(channels=["channel_1"], start="2024-01-01T00:00:00Z", end="2024-01-01T00:01:40Z")
    extras = dict(change)
    if "channels" in extras:
        request["channels"] = extras.pop("channels")
    if "view_start" in extras:
        request["start"] = extras.pop("view_start")
    if "view_end" in extras:
        request["end"] = extras.pop("view_end")
    response = view_request(client,page,request["channels"],request["start"],request["end"],**extras)
    assert response.status_code == 400 and response.get_json()["error"]
