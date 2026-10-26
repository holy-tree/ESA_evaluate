import json

import pytest

from datasets import DataError
from evaluator import evaluate_eventwise as evaluate
from conftest import at, predictions, synthetic


def test_hand_calculated():
    result = evaluate(synthetic(), predictions((12, 18), (70, 80)))
    m = result["metrics"]
    assert (m["detected_count"], m["missed_count"], m["false_positive_count"]) == (1, 1, 1)
    assert m["p_event"] == .5
    assert m["tnr"] == .875
    assert m["p_corrected"] == .4375
    assert m["recall"] == .5
    assert m["f0_5"] == pytest.approx(.44871794871794873)


@pytest.mark.parametrize("intervals,expected", [([(12, 18), (42, 48)], 1), ([], 0), ([(70, 80)], 0)])
def test_perfect_empty_false_only(intervals, expected):
    result = evaluate(synthetic(), predictions(*intervals))
    assert result["metrics"]["f0_5"] == expected


def test_full_alarm_penalty_and_many_to_many():
    result = evaluate(synthetic(), predictions((0, 100)))
    assert result["metrics"]["detected_count"] == 2
    assert len(result["predictions"]) == 1
    assert result["metrics"]["p_event"] == 1
    assert result["metrics"]["tnr"] == 0
    assert result["metrics"]["f0_5"] == 0


def test_discontinuous_multichannel_id(demo):
    dataset = demo()
    gap = [dict(channel="channel_2", start=at(25), end=at(28))]
    result = evaluate(dataset, gap)
    assert result["metrics"]["event_count"] == 2
    assert result["metrics"]["detected_count"] == 0
    assert result["metrics"]["false_positive_count"] == 1
    assert len(result["events"][0]["intervals"]) == 2
    # Channel-independent detection; channel_1's second fragment is found from channel_2.
    assert evaluate(dataset, predictions((31, 32)))["metrics"]["detected_count"] == 1


def test_overlaps_duplicates_union_and_redundancy():
    result = evaluate(synthetic(), predictions((12, 18), (12, 18), (15, 19), (70, 80), (75, 85)))
    assert len(result["predictions"]) == 2
    assert result["metrics"]["false_positive_seconds"] == 15
    result = evaluate(synthetic(), predictions((11, 12), (15, 16)))
    assert result["metrics"]["detected_count"] == 1
    assert result["metrics"]["redundant_detections"] == 1
    assert result["metrics"]["p_event"] == 1


def test_endpoints_and_points():
    dataset = synthetic()
    # Half-open predicted end at truth start does not intersect.
    assert evaluate(dataset, predictions((0, 10)))["metrics"]["detected_count"] == 0
    # Closed truth end touches predicted start.
    assert evaluate(dataset, predictions((20, 21)))["metrics"]["detected_count"] == 1
    assert evaluate(dataset, predictions((10, 10)))["metrics"]["detected_count"] == 1
    assert evaluate(dataset, predictions((30, 30)))["metrics"]["false_positive_count"] == 1
    point = synthetic([("point", 100, 100, "Anomaly")])
    assert evaluate(point, predictions((99, 100)))["metrics"]["detected_count"] == 1
    assert evaluate(point, predictions((100, 100)))["metrics"]["detected_count"] == 1


def test_category_filter_and_excluded_preserved():
    dataset = synthetic([("a", 10, 20, "Anomaly"), ("rare", 40, 50, "Rare Event"),
                         ("gap", 60, 70, "Communication Gap"), ("invalid", 80, 90, "Invalid")])
    pred = predictions((12, 18), (42, 48), (62, 68), (82, 88))
    both, anomaly = evaluate(dataset, pred), evaluate(dataset, pred, "anomaly")
    assert both["metrics"]["event_count"] == 2
    assert anomaly["metrics"]["event_count"] == 1
    assert both["metrics"]["nominal_seconds"] == anomaly["metrics"]["nominal_seconds"] == 60
    assert both["metrics"]["false_positive_count"] == anomaly["metrics"]["false_positive_count"] == 0
    assert anomaly["metrics"]["f0_5"] == 1
    assert sum(p["ignored_only"] for p in anomaly["predictions"]) == 3
    # Even a partial intersection with excluded GT avoids event FP, but normal coverage is penalized.
    mixed = evaluate(dataset, predictions((12, 18), (55, 65)), "anomaly")
    assert mixed["metrics"]["false_positive_count"] == 0
    assert mixed["metrics"]["false_positive_seconds"] == 5


def test_undefined_cases_json_safe():
    dataset = synthetic()
    dataset["labels"] = []
    result = evaluate(dataset, [])
    assert result["status"] == "not_applicable"
    assert result["metrics"]["f0_5"] is None
    assert result["metrics"]["recall"] is None
    json.dumps(result, allow_nan=False)
    full = evaluate(synthetic([("all", 0, 100, "Anomaly")]), predictions((10, 20)))
    assert full["metrics"]["tnr"] is None
    assert full["metrics"]["f0_5"] is None
    assert full["reasons"]


def test_core_rejects_invalid_scope(demo):
    with pytest.raises(DataError):
        evaluate(demo(), [dict(channel="unknown", start=at(10), end=at(20))])


def test_range_is_clipped_not_inferred(demo):
    from datasets import load_config, load_dataset
    ds = load_dataset(load_config("Mission1", "demo"), start=at(15).isoformat(), end=at(45).isoformat(), confirmed=True)
    assert ds["window"] == (at(15), at(45))
    assert min(r["start"] for r in ds["labels"]) == at(15)
    assert max(r["end"] for r in ds["labels"]) == at(45)
