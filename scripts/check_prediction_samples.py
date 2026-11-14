"""Verify demo and labels-derived real samples without writing leaderboard records."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import portion as P

from datasets import BASE, CATEGORIES, MISSIONS, load_config, load_dataset
from evaluator import evaluate, prediction_interval
from validation import parse_csv


def verify_sample(dataset, path, expected_ids=None):
    report = parse_csv(path.read_bytes(), path.name, dataset)
    assert not report["errors"], report["errors"]
    assert report["raw_count"] == 10 and report["duplicate_count"] == 0
    result = evaluate(dataset, report["records"], "both")
    matched = sum(row["matched_selected"] for row in result["predictions"])
    false = sum(row["false_positive"] for row in result["predictions"])
    ignored = sum(row["ignored_only"] for row in result["predictions"])
    assert (matched,false,ignored) == (8,2,0), (path.name,matched,false,ignored)
    rows = []
    for number,pred in enumerate(report["records"],1):
        interval = prediction_interval(pred,dataset["window"])
        ids = sorted({row["id"] for row in dataset["labels"] if row["channel"] == pred["channel"]
            and row["category"] in CATEGORIES["both"] and not (P.closed(row["start"],row["end"]) & interval).empty})
        if expected_ids is not None:
            assert set(ids) == set(expected_ids[number-1]), (path.name,number,ids,expected_ids[number-1])
        assert bool(ids) == (number <= 8), (path.name,number,ids)
        rows.append(dict(record=number,channel=pred["channel"],matched_event_ids=ids))
    return dict(file=str(path.relative_to(BASE)), records=report["raw_count"], channels=report["channel_count"],
        matched_predictions=matched,false_positive_intervals=false,ignored_predictions=ignored,
        detected_channel_events=result["metrics"]["detected_count"],macro_f0_5=result["metrics"]["f0_5"],
        defined_channels=result["aggregation"]["valid_channel_counts"]["f0_5"],
        total_target_channels=result["aggregation"]["total_channels"],rows=rows)


def main():
    manifest=json.loads((BASE/"sample_predictions"/"manifest.json").read_text(encoding="utf-8"))
    results=[]
    for mission in MISSIONS:
        demo=load_dataset(load_config(mission,"demo"))
        results.append(verify_sample(demo,BASE/"demo_data"/mission/"predictions.csv"))
        entry=manifest["missions"][mission]
        cfg=load_config(mission,"real",entry["version"])
        kwargs={} if (cfg["start"],cfg["end"]) == (entry["start"],entry["end"]) else dict(start=entry["start"],end=entry["end"],confirmed=True)
        real=load_dataset(cfg,**kwargs)
        results.append(verify_sample(real,BASE/"sample_predictions"/entry["file"],entry["expected_event_ids_by_record"]))
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
