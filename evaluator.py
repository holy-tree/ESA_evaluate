"""ESA corrected event-wise core, adapted from ESAScores (MIT).

Reference: kplabs-pl/ESA-ADB commit 27ecde187aa631512cf38a4508474b01ebeb1eda.
See THIRD_PARTY_LICENSES.md. No time-grid expansion or one-to-one matching.
"""
from collections import defaultdict

import portion as P

from datasets import CATEGORIES, DataError, iso

REFERENCE = "kplabs-pl/ESA-ADB@27ecde187aa631512cf38a4508474b01ebeb1eda"


def union(intervals):
    return P.Interval(*intervals)


def duration(intervals):
    return sum((i.upper.value - i.lower.value) / 1e9 for i in intervals)


def serialize_intervals(intervals):
    return [dict(start=iso(i.lower), end=iso(i.upper), left_closed=i.left == P.CLOSED,
                 right_closed=i.right == P.CLOSED, point=i.lower == i.upper) for i in intervals]


def prediction_interval(record, window):
    a, b = record["start"], record["end"]
    # Official binary series is [on, off), except final alarm extends to closed full_range end.
    return P.closed(a, b) if a == b or b == window[1] else P.closedopen(a, b)


def evaluate(dataset, predictions, category_mode="both"):
    if category_mode not in CATEGORIES:
        raise DataError("事件类别选择无效")
    window = dataset["window"]
    if not window:
        raise DataError("评分需要固定评测窗口")
    if dataset["config"].get("label_errors"):
        raise DataError("当前窗口包含无效真实标签，无法评分：" + dataset["config"]["label_errors"][0])
    for p in predictions:
        if p["channel"] not in dataset["channels"] or not window[0] <= p["start"] <= p["end"] <= window[1]:
            raise DataError("预测未通过当前 Mission 通道与时间范围校验")
    labels = dataset["labels"]
    events = defaultdict(list)
    for row in labels:
        if row["category"] in CATEGORIES[category_mode]:
            events[row["id"]].append(row)
    pred_union = union(prediction_interval(p, window) for p in predictions)
    pred_atoms = list(pred_union)
    matched = [False] * len(pred_atoms)
    details, detected, redundant = [], 0, 0
    for event_id, rows in events.items():
        gt = union(P.closed(r["start"], r["end"]) for r in rows)
        hits = [n for n, p in enumerate(pred_atoms) if not (p & gt).empty]
        for n in hits:
            matched[n] = True
        for segment in gt:
            redundant += max(0, sum(not (p & segment).empty for p in pred_atoms) - 1)
        detected += bool(hits)
        details.append(dict(id=event_id, category=rows[0]["category"],
                            channels=sorted({r["channel"] for r in rows}), detected=bool(hits),
                            prediction_indices=hits, intervals=serialize_intervals(gt)))
    all_gt = union(P.closed(r["start"], r["end"]) for r in labels)
    false_flags = [not match and (p & all_gt).empty for p, match in zip(pred_atoms, matched)]
    fp = sum(false_flags)
    nominal = P.closed(*window) - all_gt
    nominal_seconds = duration(nominal)
    fp_seconds = duration(nominal & pred_union)
    tnr = max(0.0, min(1.0, 1 - fp_seconds / nominal_seconds)) if nominal_seconds > 0 else None
    p_event = detected / (detected + fp) if detected + fp else 0.0
    recall = detected / len(events) if events else None
    corrected = p_event * tnr if tnr is not None else None
    reasons = []
    if not events:
        reasons.append("没有可评测真实事件，Recall 与 F0.5 不适用")
    if nominal_seconds == 0:
        reasons.append("正常时长为 0，TNR、修正精确率与 F0.5 不适用")
    f_score = None
    if not reasons:
        divider = .25 * corrected + recall
        f_score = 1.25 * corrected * recall / divider if divider else 0.0
    config = {k: v for k, v in dataset["config"].items() if k != "paths"}
    config.update(category_mode=category_mode, selected_categories=CATEGORIES[category_mode],
                  beta=0.5, timezone="UTC", reference=REFERENCE,
                  prediction_endpoints="[start,end); point closed; full_range end closed",
                  ground_truth_endpoints="closed", scope="event discovery, not channel localization")
    return dict(status="not_applicable" if reasons else "ok", reasons=reasons, config=config,
                metrics=dict(f0_5=f_score, p_event=p_event, tnr=tnr, p_corrected=corrected, recall=recall,
                             event_count=len(events), detected_count=detected, missed_count=len(events)-detected,
                             false_positive_count=fp, nominal_seconds=nominal_seconds,
                             false_positive_seconds=fp_seconds, redundant_detections=redundant),
                events=details,
                predictions=[dict(interval=serialize_intervals(p)[0], matched_selected=matched[n],
                                  false_positive=false_flags[n],
                                  ignored_only=not matched[n] and not false_flags[n]) for n, p in enumerate(pred_atoms)],
                input_predictions=[dict(channel=p["channel"], start=iso(p["start"]), end=iso(p["end"])) for p in predictions],
                label_fragments=[dict(id=r["id"], channel=r["channel"], category=r["category"],
                                      start=iso(r["start"]), end=iso(r["end"]),
                                      selected=r["category"] in CATEGORIES[category_mode]) for r in labels])
