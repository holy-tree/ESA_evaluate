"""Read-only audit of local ESA labels and, optionally, all telemetry channels."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import portion as P

from datasets import MISSIONS, iso, load_config, read_csv, timestamp


def audit_labels(cfg, channel_rows):
    _, labels = read_csv(cfg["paths"]["labels"])
    _, types = read_csv(cfg["paths"]["anomaly_types"])
    categories = {r["id"]: r["category"] for r in types}
    targets = {r["channel"] for r in channel_rows if r["target"].lower() in ("yes", "true", "1")}
    scopes = {}
    for scope in ("all_channels", "target_channels"):
        rows = [r for r in labels if scope == "all_channels" or r["channel"] in targets]
        by_category = defaultdict(list)
        issues, duplicates, seen = [], 0, set()
        for line, r in enumerate(labels, 2):
            if scope == "target_channels" and r["channel"] not in targets:
                continue
            key = tuple(r.get(k) for k in ("id", "channel", "starttime", "endtime"))
            duplicates += key in seen
            seen.add(key)
            category = r.get("category", categories.get(r["id"], "MISSING"))
            a = b = None
            problem = None
            try:
                a, b = timestamp(r["starttime"]), timestamp(r["endtime"])
                if a > b:
                    problem = "reversed_interval"
            except (ValueError, OverflowError):
                problem = "invalid_time"
            if category == "MISSING" or not category:
                problem = "missing_category"
            if problem:
                issues.append(dict(line=line, id=r["id"], channel=r["channel"], category=category, reason=problem))
            by_category[category].append(dict(id=r["id"], channel=r["channel"], start=a, end=b, valid=not problem))
        summaries = {}
        for category, fragments in by_category.items():
            events = defaultdict(list)
            for r in fragments:
                events[r["id"]].append(r)
            valid_fragments = [r for r in fragments if r["valid"]]
            valid_segments = sum(len(P.Interval(*[P.closed(r["start"], r["end"]) for r in rr if r["valid"]])) for rr in events.values())
            summaries[category] = dict(event_ids=len(events), label_rows=len(fragments), valid_rows=len(valid_fragments),
                invalid_rows=len(fragments)-len(valid_fragments),
                events_with_any_invalid_rows=sum(any(not r["valid"] for r in rr) for rr in events.values()),
                events_with_all_rows_valid=sum(all(r["valid"] for r in rr) for rr in events.values()),
                merged_time_segments_over_valid_rows=valid_segments,
                multichannel_events=sum(len({r["channel"] for r in rr}) > 1 for rr in events.values()),
                point_label_rows=sum(r["valid"] and r["start"] == r["end"] for r in fragments))
        scopes[scope] = dict(label_rows=len(rows), event_ids=len({r["id"] for r in rows}),
                             duplicate_rows=duplicates, categories=summaries, issues=issues)
    return dict(types_rows=len(types), types_categories=dict(Counter(r["category"] for r in types)),
        types_ids_without_labels=sorted(set(categories)-{r["id"] for r in labels}),
        scopes=scopes)


def telemetry_channel(path, target):
    with ZipFile(path) as z:
        uncompressed = sum(i.file_size for i in z.infolist())
    frame = pd.read_pickle(path)
    idx = frame.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError(f"{path} has no DatetimeIndex")
    idx = (idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")).as_unit("ns")
    delta = np.diff(idx.asi8)
    sorted_ = idx.is_monotonic_increasing
    duplicate_mask = np.r_[False, delta == 0] if sorted_ else idx.duplicated()
    numeric = all(pd.api.types.is_numeric_dtype(t) for t in frame.dtypes)
    values = frame.to_numpy(copy=False)
    missing_cells = int(pd.isna(values).sum())
    inf_cells = int(np.isinf(values).sum()) if numeric else None
    finite_rows = np.isfinite(values).all(axis=1) if numeric else ~pd.isna(values).any(axis=1)
    mechanically_usable = int(np.count_nonzero(finite_rows & ~idx.isna() & ~duplicate_mask))
    sampled = delta[np.linspace(0, len(delta)-1, min(10000, len(delta)), dtype=int)] / 1e9 if len(delta) else np.array([])
    return dict(channel=path.stem, target=target, samples=len(frame), value_columns=len(frame.columns),
        start=iso(idx.min()), end=iso(idx.max()), compressed_bytes=path.stat().st_size, uncompressed_pickle_bytes=uncompressed,
        numeric=numeric, missing_value_cells=missing_cells, infinite_value_cells=inf_cells,
        missing_timestamps=int(idx.isna().sum()), duplicate_timestamps=int(duplicate_mask.sum()),
        sorted=bool(sorted_), backward_timestamp_steps=int((delta < 0).sum()),
        mechanically_usable_rows=mechanically_usable,
        sampled_step_median_seconds=float(np.median(sampled)) if len(sampled) else None,
        max_timestamp_gap_seconds=float(delta.max()/1e9) if len(delta) else None)


def aggregate(rows):
    return dict(channels=len(rows), samples=sum(r["samples"] for r in rows),
        start=iso(min(timestamp(r["start"]) for r in rows)), end=iso(max(timestamp(r["end"]) for r in rows)),
        compressed_bytes=sum(r["compressed_bytes"] for r in rows),
        uncompressed_pickle_bytes=sum(r["uncompressed_pickle_bytes"] for r in rows),
        missing_value_cells=sum(r["missing_value_cells"] for r in rows),
        infinite_value_cells=sum(r["infinite_value_cells"] or 0 for r in rows),
        missing_timestamps=sum(r["missing_timestamps"] for r in rows),
        duplicate_timestamps=sum(r["duplicate_timestamps"] for r in rows),
        largest_timestamp_gap_days=max(r["max_timestamp_gap_seconds"] or 0 for r in rows)/86400,
        unsorted_channels=sum(not r["sorted"] for r in rows),
        non_numeric_channels=sum(not r["numeric"] for r in rows),
        mechanically_usable_rows=sum(r["mechanically_usable_rows"] for r in rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=MISSIONS, required=True)
    parser.add_argument("--telemetry", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inspect-channel", help="Show value types and a few values of one channel")
    parser.add_argument("--summarize-report", type=Path, help="Summarize an existing audit JSON without rescanning telemetry")
    args = parser.parse_args()
    if args.summarize_report:
        report = json.loads(args.summarize_report.read_text(encoding="utf-8"))
        stats = report["telemetry"]["channels"]
        print(json.dumps(dict(mission=report["mission"],
            all_channels=aggregate(stats), target_channels=aggregate([r for r in stats if r["target"]]),
            targets_numeric_sample_count=sum(r["samples"] for r in stats if r["target"] and r["numeric"]),
            targets_non_numeric_sample_count=sum(r["samples"] for r in stats if r["target"] and not r["numeric"]),
            missing_category_ids=report["labels"]["scopes"]["all_channels"]["categories"].get("MISSING", {}).get("event_ids", 0),
            types_ids_without_labels=report["labels"]["types_ids_without_labels"]), ensure_ascii=False, indent=2))
        return
    cfg = load_config(args.mission, "real")
    if args.inspect_channel:
        path = Path(cfg["paths"]["labels"]).parent / "channels" / f"{args.inspect_channel}.zip"
        frame = pd.read_pickle(path)
        print("dtypes:", frame.dtypes.to_dict())
        print("value_types:", [type(v).__name__ for v in frame.iloc[:10, 0]])
        print("values:", frame.iloc[:10, 0].tolist())
        return
    _, channels = read_csv(cfg["paths"]["channels"])
    report = dict(mission=args.mission, version=cfg["version"], channels=len(channels),
        target_channels=sum(r["target"].lower() in ("yes", "true", "1") for r in channels),
        labels=audit_labels(cfg, channels))
    if args.telemetry:
        folder = Path(cfg["paths"]["labels"]).parent / "channels"
        stats = []
        for number, r in enumerate(channels, 1):
            print(f"{args.mission} [{number}/{len(channels)}] {r['channel']}", file=sys.stderr, flush=True)
            stats.append(telemetry_channel(folder / f"{r['channel']}.zip", r["target"].lower() in ("yes", "true", "1")))
        report["telemetry"] = dict(all_channels=aggregate(stats), target_channels=aggregate([r for r in stats if r["target"]]), channels=stats)
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k:v for k,v in report.items() if k not in ("labels", "telemetry")}
    summary["label_scopes"] = {scope: {k:v for k,v in values.items() if k != "issues"} for scope,values in report["labels"]["scopes"].items()}
    if "telemetry" in report:
        summary["telemetry"] = {k:v for k,v in report["telemetry"].items() if k != "channels"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
