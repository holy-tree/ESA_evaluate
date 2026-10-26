import json

import pytest

from conftest import at, synthetic
from evaluator import evaluate


def label(event_id, channel, start, end, category="Anomaly"):
    return dict(id=event_id, channel=channel, start=at(start), end=at(end), category=category)


def prediction(channel, start, end):
    return dict(channel=channel, start=at(start), end=at(end))


def scope(rows):
    ds = synthetic()
    ds["labels"] = rows
    return ds


def test_macro_directly_averages_channel_scores_not_mean_precision_recall():
    ds = scope([label("shared", "channel_1", 10, 20), label("shared", "channel_2", 10, 20), label("second", "channel_2", 40, 50)])
    result = evaluate(ds, [prediction("channel_1", 12, 18), prediction("channel_2", 12, 18), prediction("channel_2", 70, 80)])
    first, second = result["channel_results"]
    assert first["metrics"]["f0_5"] == 1
    assert second["metrics"]["f0_5"] == pytest.approx(.44871794871794873)
    m = result["metrics"]
    assert m["f0_5"] == pytest.approx((1+.44871794871794873)/2)
    assert m["p_event"] == .75
    assert m["tnr"] == .9375
    assert m["p_corrected"] == .71875
    assert m["recall"] == .75
    recomputed = 1.25*m["p_corrected"]*m["recall"]/(.25*m["p_corrected"]+m["recall"])
    assert m["f0_5"] != pytest.approx(recomputed)
    assert m["p_corrected"] != m["p_event"]*m["tnr"]
    assert m["event_count"] == 3 and m["unique_event_count"] == 2
    assert (m["detected_count"], m["missed_count"], m["false_positive_count"]) == (2, 1, 1)
    assert result["aggregation"]["valid_channel_counts"]["f0_5"] == 2
    assert result["config"]["aggregation"] == "channel_macro_mean"


def test_predictions_do_not_hit_another_channel_and_empty_channel_not_dropped():
    ds = scope([label("same", "channel_1", 10, 20), label("same", "channel_2", 40, 50)])
    result = evaluate(ds, [prediction("channel_2", 12, 18)])
    assert result["metrics"]["detected_count"] == 0
    assert result["metrics"]["missed_count"] == 2
    assert result["metrics"]["false_positive_count"] == 1
    assert result["metrics"]["f0_5"] == 0
    assert len(result["channel_results"]) == 2
    assert all(row["metrics"]["f0_5"] == 0 for row in result["channel_results"])


def test_no_event_weighting_and_unpredicted_channel_is_evaluated():
    rows = [label("a", "channel_1", 10, 20)]
    rows += [label(f"b{i}", "channel_2", 30+i*2, 31+i*2) for i in range(9)]
    result = evaluate(scope(rows), [prediction("channel_1", 12, 18)])
    assert result["metrics"]["recall"] == .5
    assert result["metrics"]["f0_5"] == .5
    assert result["metrics"]["detected_count"] == 1
    assert result["metrics"]["event_count"] == 10
    assert result["channel_results"][1]["metrics"]["missed_count"] == 9


def test_no_gt_channel_is_null_for_f_but_its_false_alarms_are_visible():
    result = evaluate(scope([label("a", "channel_1", 10, 20)]),
                      [prediction("channel_1", 12, 18), prediction("channel_2", 12, 18)])
    background = result["channel_results"][1]
    assert background["metrics"]["f0_5"] is None
    assert background["metrics"]["recall"] is None
    assert background["metrics"]["false_positive_count"] == 1
    assert result["metrics"]["f0_5"] == 1
    assert result["metrics"]["false_positive_count"] == 1
    assert result["aggregation"]["valid_channel_counts"]["f0_5"] == 1
    assert result["aggregation"]["valid_channel_counts"]["p_event"] == 2
    assert result["aggregation"]["excluded_channels"]["f0_5"] == ["channel_2"]
    assert result["warnings"]


def test_no_normal_duration_and_no_gt_are_never_fabricated_scores():
    result = evaluate(scope([label("full", "channel_1", 0, 100), label("b", "channel_2", 40, 50)]),
                      [prediction("channel_1", 12, 18), prediction("channel_2", 42, 48)])
    assert result["channel_results"][0]["metrics"]["tnr"] is None
    assert result["channel_results"][0]["metrics"]["f0_5"] is None
    assert result["metrics"]["f0_5"] == 1
    assert result["aggregation"]["valid_channel_counts"]["recall"] == 2
    assert result["aggregation"]["valid_channel_counts"]["tnr"] == 1
    undefined = evaluate(scope([]), [])
    assert undefined["status"] == "not_applicable"
    assert undefined["metrics"]["f0_5"] is None
    assert undefined["aggregation"]["valid_channel_counts"]["f0_5"] == 0
    json.dumps(undefined, allow_nan=False)


def test_excluded_categories_are_local_to_their_own_channel():
    ds = scope([label("a", "channel_1", 10, 20), label("rare", "channel_1", 60, 70, "Rare Event"), label("b", "channel_2", 40, 50)])
    result = evaluate(ds, [prediction("channel_1", 12, 18), prediction("channel_2", 62, 68)], "anomaly")
    a, b = result["channel_results"]
    assert a["metrics"]["nominal_seconds"] == 80
    assert b["metrics"]["nominal_seconds"] == 90
    assert b["metrics"]["false_positive_count"] == 1
    assert b["metrics"]["false_positive_seconds"] == 6
    assert b["predictions"][0]["false_positive"]


def test_single_channel_hand_case_and_duplicate_predictions():
    ds = scope([label("a", "channel_1", 10, 20), label("b", "channel_1", 40, 50)])
    ds["channels"] = ["channel_1"]
    pred = [prediction("channel_1", 12, 18), prediction("channel_1", 70, 80)]
    result = evaluate(ds, pred+pred)
    assert result["metrics"]["f0_5"] == pytest.approx(.44871794871794873)
    assert result["metrics"]["false_positive_count"] == 1
    assert len(result["predictions"]) == 2
