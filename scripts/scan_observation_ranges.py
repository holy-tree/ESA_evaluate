"""One-off telemetry scan; never imported by the web scoring path."""
import argparse
import json
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from datasets import MISSIONS, DataError, iso, load_config, read_csv


def channel_bounds(path):
    """Load only one channel at a time; discard values after inspecting its index."""
    frame = pd.read_pickle(path)
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.empty or index.hasnans:
        raise DataError(f"{path.name} 必须具有非空且无 NaT 的 DatetimeIndex")
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    return dict(channel=path.stem, start=iso(index.min()), end=iso(index.max()),
                sample_count=len(index), sorted=index.is_monotonic_increasing)


def scan(cfg):
    _, rows = read_csv(cfg["paths"]["channels"])
    channels = cfg.get("target_channels") or [r["channel"] for r in rows if r["target"].lower() in ("yes", "true", "1")]
    if not channels:
        raise DataError("没有明确配置目标通道")
    folder = Path(cfg["paths"]["labels"]).parent / "channels"
    bounds = []
    for number, channel in enumerate(channels, 1):
        path = folder / f"{channel}.zip"
        print(f"{cfg['mission_id']} [{number}/{len(channels)}] {channel}", file=sys.stderr, flush=True)
        bounds.append(channel_bounds(path))
    starts = [pd.Timestamp(row["start"]) for row in bounds]
    ends = [pd.Timestamp(row["end"]) for row in bounds]
    return dict(mission_id=cfg["mission_id"], version=cfg["version"],
                method="target_channel_timestamp_envelope", timezone="UTC; naive telemetry timestamps interpreted as UTC",
                channel_count=len(bounds), start=iso(min(starts)), end=iso(max(ends)),
                latest_channel_start=iso(max(starts)), earliest_channel_end=iso(min(ends)),
                channels=bounds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", choices=MISSIONS, required=True)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.mission, "real")
    if args.inspect:
        _, rows = read_csv(cfg["paths"]["channels"])
        channel = next(r["channel"] for r in rows if r["target"].lower() in ("yes", "true", "1"))
        path = Path(cfg["paths"]["labels"]).parent / "channels" / f"{channel}.zip"
        with ZipFile(path) as archive:
            for entry in archive.infolist():
                with archive.open(entry) as stream:
                    print(entry.filename, "uncompressed_bytes=", entry.file_size, "header=", repr(stream.read(80)))
        frame = pd.read_pickle(path)
        print("type=", type(frame).__name__, "shape=", frame.shape, "index_type=", type(frame.index).__name__, "index_dtype=", frame.index.dtype)
        print("first_index=", frame.index[0], "last_index=", frame.index[-1])
        return
    print(json.dumps(scan(cfg), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
