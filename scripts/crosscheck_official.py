"""Run the pinned, unmodified upstream EW block against representative interval cases.

Needs network on this explicit verification command only; app/tests work offline.
Upstream affiliation block is removed, because it is a separate unused metric and
can be undefined even where EW is defined. No EW statements are changed.
"""
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import portion as P

from datasets import load_config, load_dataset, timestamp
from evaluator import evaluate

COMMIT = "27ecde187aa631512cf38a4508474b01ebeb1eda"
ROOT = f"https://raw.githubusercontent.com/kplabs-pl/ESA-ADB/{COMMIT}/timeeval/metrics/"


def official_class():
    source = urlopen(ROOT + "ESA_ADB_metrics.py", timeout=40).read().decode()
    utils = urlopen(ROOT + "utils.py", timeout=40).read().decode()
    namespace = {"Metric": object}
    exec(compile(utils, "official-utils.py", "exec"), namespace)
    source = "\n".join(line for line in source.splitlines() if not line.startswith("from ."))
    source = source.split("        # Affiliation-based")[0] + "\n        return result_dict\n"
    exec(compile(source, "official-ESA_ADB_metrics.py", "exec"), namespace)
    return namespace["ESAScores"]


def main():
    Official = official_class()
    at = lambda s: timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(seconds=s)
    # Cases are alarm on/off sequences; conversion uses the actual pinned utils.py.
    cases = [
        ("hand-calculated", [(12, 18), (70, 80)], "both"),
        ("perfect", [(12, 18), (42, 48)], "both"),
        ("empty", [], "both"),
        ("pure-false-positive", [(70, 80)], "both"),
        ("full-window", [(0, 100)], "both"),
        ("right-open-touch", [(0, 10)], "both"),
        ("closed-truth-touch", [(20, 21)], "both"),
        ("repeated-hits", [(11, 12), (15, 16)], "both"),
        ("excluded-gap", [(12, 18), (61, 68)], "both"),
        ("excluded-rare", [(12, 18), (42, 48)], "anomaly"),
        ("point-at-end", [(100, 100)], "both"),
        ("overlapping-inputs", [(12, 18), (15, 19), (12, 18)], "both"),
        ("discontinuous-gap", [(25, 28)], "both"),
        ("internal-point", [(30, 30)], "both"),
    ]
    for name, intervals, mode in cases:
        ds = load_dataset(load_config("Mission1", "demo"))
        raw = [("a", 10, 20, "Anomaly"), ("b", 40, 50, "Rare Event"),
               ("gap", 60, 70, "Communication Gap"), ("invalid", 80, 90, "Invalid"),
               ("point", 100, 100, "Anomaly")]
        if name in ("hand-calculated", "perfect"):
            raw = [("a", 10, 20, "Anomaly"), ("b", 40, 50, "Anomaly")]
        if name == "discontinuous-gap":
            raw.append(("a", 30, 35, "Anomaly"))
        if name == "internal-point":
            raw.append(("internal", 30, 30, "Anomaly"))
        ds["labels"] = [dict(id=i, channel="channel_1", start=at(a), end=at(b), category=c) for i,a,b,c in raw]
        predictions = [dict(channel="channel_2", start=at(a), end=at(b)) for a,b in intervals]
        ours = evaluate(ds, predictions, mode)["metrics"]
        # Independently form the official binary sequence (merge overlaps with portion).
        events = P.Interval(*[P.closed(at(a), at(b)) if a == b or b == 100 else P.closedopen(at(a), at(b)) for a,b in intervals])
        vector = [[at(0), 0]]
        for event in events:
            if event.lower == at(0):
                vector = []
            vector.append([event.lower, 1])
            if event.upper < at(100):
                vector.append([event.upper, 0])
        if vector[-1][0] < at(100):
            vector.append([at(100), vector[-1][1]])
        truth = pd.DataFrame([[r["id"],r["start"],r["end"],r["category"]] for r in ds["labels"]],
                             columns=["ID", "StartTime", "EndTime", "Category"])
        metric = Official(betas=.5, full_range=(at(0), at(100)),
                          select_labels={"Category": ["Anomaly", "Rare Event"] if mode == "both" else ["Anomaly"]})
        if name == "internal-point":
            # Internal closed points are an interval-input extension: compare directly
            # to the unchanged upstream EW block, injecting its already-converted events.
            namespace = metric.score.__globals__
            converter = namespace["convert_time_series_to_events"]
            namespace["convert_time_series_to_events"] = lambda _: events
        expected = metric.score(truth, np.array(vector, dtype=object))
        if name == "internal-point":
            namespace["convert_time_series_to_events"] = converter
        for upstream, local in (("EW_precision", "p_corrected"), ("EW_recall", "recall"), ("EW_F_0.50", "f0_5")):
            assert np.isclose(expected[upstream], ours[local], rtol=0, atol=1e-12), (name, upstream, expected, ours)
        print(f"PASS {name}: F0.5={ours['f0_5']:.12f}")
    print(f"Official cross-check passed: {len(cases)} cases, reference {COMMIT}")


if __name__ == "__main__":
    main()
