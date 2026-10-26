"""Windowed raw-telemetry visualization, separate from label-only scoring."""
from collections import OrderedDict, defaultdict
import hashlib
from pathlib import Path
from pickle import UnpicklingError
from threading import Lock
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import portion as P

from datasets import CATEGORIES, DataError, iso, timestamp

SCALES = {"day": ("D", pd.DateOffset(days=1), 2400),
          "month": ("MS", pd.DateOffset(months=1), 3720),
          "year": ("YS", pd.DateOffset(years=1), 5760)}


def display_window(window, date, scale):
    if scale not in SCALES:
        raise DataError("图表时间尺度无效，请选择天、月或年")
    try:
        requested_date = timestamp(date).normalize()
        anchor = requested_date
        if scale == "month":
            anchor = anchor.replace(day=1)
        elif scale == "year":
            anchor = anchor.replace(month=1, day=1)
        offset = SCALES[scale][1]
        end = anchor + offset
    except (ValueError, OverflowError) as exc:
        raise DataError(f"图表日期无效：{exc}") from exc
    start, stop = max(anchor, window[0]), min(end, window[1])
    if start > stop:
        raise DataError("图表日期超出本次评测范围")
    previous, following = anchor - offset, anchor + offset
    return dict(start=start, end=stop, anchor=anchor, requested_date=requested_date,
                previous_date=max(previous, window[0]).strftime("%Y-%m-%d") if anchor > window[0] else None,
                next_date=following.strftime("%Y-%m-%d") if following <= window[1] else None)


def select_extrema(values, max_points):
    """Keep actual samples (first/last/min/max per bucket), never average values."""
    finite = np.flatnonzero(np.isfinite(values))
    if len(finite) <= max_points:
        return finite
    selected = []
    for bucket in np.array_split(finite, max(1, max_points // 4)):
        sample = values[bucket]
        selected.extend((bucket[0], bucket[-1], bucket[np.argmin(sample)], bucket[np.argmax(sample)]))
    return np.unique(selected)


def select_states(values, max_points, breaks):
    """Retain state transition boundaries, without treating state codes as magnitudes."""
    finite = np.flatnonzero(np.isfinite(values))
    if len(finite) <= max_points:
        return finite
    transitions = np.flatnonzero(values[1:] != values[:-1])
    gaps = np.flatnonzero(breaks)
    candidates = np.unique(np.r_[finite[0], finite[-1], transitions, transitions+1, gaps, gaps-1])
    candidates = candidates[(candidates >= 0) & (candidates < len(values))]
    candidates = candidates[np.isfinite(values[candidates])]
    if len(candidates) > max_points:
        candidates = candidates[np.linspace(0, len(candidates)-1, max_points, dtype=int)]
    return candidates


def event_annotations(dataset, channel, a, b, support, sample_times, category_mode):
    """True-label identities, not prediction rows; preserve disjoint fragments."""
    labels = dataset.get("labels", [])
    ids = sorted(set(dataset.get("event_ids", [])) | {row["id"] for row in labels})
    cfg = dataset["config"]
    namespace = f"{cfg['mission_id']}|{cfg['version']}|{cfg['source']}"
    base = int(hashlib.sha256(namespace.encode()).hexdigest()[:8], 16) % 360
    colors = {event_id: f"hsl({(base + index * 137.507764) % 360:.3f}, 78%, 32%)"
              for index, event_id in enumerate(ids)}
    groups = defaultdict(list)
    for row in labels:
        if row["channel"] == channel:
            groups[row["id"]].append(row)
    catalogue, memberships = [], [[] for _ in sample_times]
    curve_support = P.Interval(*support) & P.closed(a, b)
    for event_id, rows in groups.items():
        truth = P.Interval(*[P.closed(row["start"].value, row["end"].value) for row in rows])
        visible = truth & curve_support
        if visible.empty:
            continue
        intervals = []
        for interval in visible:
            intervals.append(dict(start=(interval.lower-a)/1e9, end=(interval.upper-a)/1e9,
                point=interval.lower == interval.upper,
                start_time=iso(pd.Timestamp(interval.lower, tz="UTC")),
                end_time=iso(pd.Timestamp(interval.upper, tz="UTC"))))
            left = np.searchsorted(sample_times, interval.lower, side="left")
            right = np.searchsorted(sample_times, interval.upper, side="right")
            for position in range(left, right):
                memberships[position].append(event_id)
        catalogue.append(dict(id=event_id, key=f"{namespace}|{event_id}", color=colors[event_id],
            event_start=iso(min(row["start"] for row in rows)),
            category=rows[0]["category"], selected=rows[0]["category"] in CATEGORIES[category_mode], intervals=intervals))
    return catalogue, memberships


class TelemetryStore:
    """Byte-bounded LRU; keys include Mission, version, source and file stamp."""

    def __init__(self, max_bytes=512 * 1024 * 1024):
        self.max_bytes = max(0, int(max_bytes))
        self.cached_bytes = 0
        self._cache = OrderedDict()
        self._lock = Lock()

    def series(self, dataset, channel):
        if channel not in dataset["channels"]:
            raise DataError("图表通道不属于当前 Mission 的目标通道")
        cfg = dataset["config"]
        folder = Path(cfg["paths"]["labels"]).parent
        path = folder / "channels" / f"{channel}.zip" if cfg["source"] == "real" else folder / "telemetry.csv"
        try:
            stat = path.stat()
            key = (cfg["mission_id"], cfg["version"], cfg["source"], channel,
                   str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            with self._lock:
                if key in self._cache:
                    self._cache.move_to_end(key)
                    return self._cache[key][0]
                data = self._read(key)
                size = data["times"].nbytes + data["values"].nbytes + sum(len(s.encode("utf-8")) for s in data["categories"])
                # Old versions of a changed channel should not consume the budget.
                for old in list(self._cache):
                    if old[:5] == key[:5]:
                        self.cached_bytes -= self._cache.pop(old)[1]
                while self._cache and self.cached_bytes + size > self.max_bytes:
                    self.cached_bytes -= self._cache.popitem(last=False)[1][1]
                if size <= self.max_bytes:
                    self._cache[key] = (data, size)
                    self.cached_bytes += size
                return data
        except DataError:
            raise
        except (OSError, ValueError, TypeError, BadZipFile, EOFError, ImportError, UnpicklingError) as exc:
            raise DataError(f"无法读取 {channel} 原始序列：{exc}。评分结果不受原始曲线读取失败影响。") from exc

    @staticmethod
    def _read(key):
        _, _, source, channel, path, _, _ = key
        if source == "real":
            frame = pd.read_pickle(path)
            if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
                raise DataError("原始通道必须为带 DatetimeIndex 的 pandas DataFrame")
            if channel in frame:
                series = frame[channel]
            elif len(frame.columns) == 1:
                series = frame.iloc[:, 0]
            else:
                raise DataError(f"原始文件无法识别 {channel} 数值列")
            index = frame.index
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            if not {"channel", "time", "value"}.issubset(frame.columns):
                raise DataError("演示序列需要 channel,time,value 列")
            frame = frame[frame["channel"] == channel]
            series = frame["value"]
            index = pd.DatetimeIndex(pd.to_datetime(frame["time"], utc=True, format="ISO8601"))
        if index.empty or index.hasnans or not index.is_monotonic_increasing:
            raise DataError("原始序列的时间索引必须非空、有序且不含 NaT")
        index = (index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")).as_unit("ns")
        times = index.asi8
        categories = []
        if pd.api.types.is_numeric_dtype(series.dtype):
            values = series.to_numpy(copy=False)
            kind = "numeric"
        else:
            codes, labels = pd.factorize(series, sort=False)
            values = codes.astype(np.float32)
            values[codes < 0] = np.nan
            categories = [str(label) for label in labels]
            kind = "categorical"
        # Only a display connection rule, not a judgement about missing/abnormal data.
        positions = np.linspace(0, len(times)-2, min(10000, max(0, len(times)-1)), dtype=int)
        deltas = times[positions+1] - times[positions]
        positive = deltas[deltas > 0]
        gap_ns = max(int(np.median(positive)) * 10, 1_000_000_000) if len(positive) else 1_000_000_000
        reference = dict(center=0.0, scale=1.0)
        if kind == "numeric":
            sample = values[np.linspace(0, len(values)-1, min(20000, len(values)), dtype=int)]
            sample = sample[np.isfinite(sample)]
            if len(sample):
                q25, center, q75 = np.percentile(sample, [25, 50, 75])
                spread = float(q75-q25) or float(np.std(sample)) or 1.0
                reference = dict(center=float(center), scale=spread)
        return dict(times=times, values=values, kind=kind, categories=categories, gap_ns=gap_ns,
                    reference=reference)

    def window(self, dataset, channel, date, scale, predictions=(), max_points=8000, category_mode="both"):
        if max_points < 4 or max_points > 20000:
            raise DataError("图表显示点数上限须在 4—20000 之间")
        if category_mode not in CATEGORIES:
            raise DataError("图表事件类别无效")
        visible = display_window(dataset["window"], date, scale)
        series = self.series(dataset, channel)
        a, b = visible["start"].value, visible["end"].value
        left = np.searchsorted(series["times"], a, side="left")
        right = np.searchsorted(series["times"], b, side="right")
        times = series["times"][left:right]
        values = np.asarray(series["values"][left:right], dtype=float)
        finite = np.isfinite(values)
        # Prefix IDs prevent a decimated line from crossing any original gap/NaN.
        breaks = np.zeros(len(times), dtype=np.int64)
        if len(times) > 1:
            breaks[1:] = ((np.diff(times) > series["gap_ns"]) | ~finite[1:] | ~finite[:-1])
        picked = select_states(values, max_points, breaks) if series["kind"] == "categorical" else select_extrema(values, max_points)
        group_ids = np.cumsum(breaks)
        segments, support, current = [], [], []
        previous_group = None
        current_times = []
        for i in picked:
            if previous_group is not None and group_ids[i] != previous_group:
                segments.append(current)
                support.append(P.closed(current_times[0], current_times[-1]))
                current, current_times = [], []
            current.append([(int(times[i])-a)/1e9, float(values[i]), iso(pd.Timestamp(int(times[i]), tz="UTC")), int(left+i)])
            current_times.append(int(times[i]))
            previous_group = group_ids[i]
        if current:
            segments.append(current)
            support.append(P.closed(current_times[0], current_times[-1]))
        events, memberships = event_annotations(dataset, channel, a, b, support, times[picked], category_mode)
        for point, event_ids in zip((point for segment in segments for point in segment), memberships):
            point.append(event_ids)
        pred = []
        for row in predictions:
            if row["channel"] != channel:
                continue
            start, end = timestamp(row["start"]).value, timestamp(row["end"]).value
            closed_end = end == dataset["window"][1].value
            pred.append(P.closed(start, end) if start == end or closed_end else P.closedopen(start, end))
        # Merge overlapping predictions once, and shade only where a curve is drawn.
        overlap = P.Interval(*pred) & P.Interval(*support) & P.closed(a, b)
        shading = [dict(start=(i.lower-a)/1e9, end=(i.upper-a)/1e9, point=i.lower == i.upper,
                        right_closed=i.right == P.CLOSED,
                        start_time=iso(pd.Timestamp(i.lower, tz="UTC")), end_time=iso(pd.Timestamp(i.upper, tz="UTC"))) for i in overlap]
        span = max((b-a)/1e9, 1e-9)
        frequency = {"day": "h", "month": "D", "year": "MS"}[scale]
        ticks = {a: visible["start"], b: visible["end"]}
        for tick in pd.date_range(visible["anchor"], visible["end"], freq=frequency):
            if a <= tick.value <= b:
                ticks[tick.value] = tick
        tick_format = {"day": "%H:%M:%S", "month": "%m-%d", "year": "%Y-%m"}[scale]
        return dict(mission=dataset["config"]["mission_id"], version=dataset["config"]["version"],
                    source=dataset["config"]["source"], channel=channel, scale=scale, timezone="UTC",
                    window_start=iso(visible["start"]), window_end=iso(visible["end"]), span_seconds=span,
                    date=visible["requested_date"].strftime("%Y-%m-%d"), previous_date=visible["previous_date"], next_date=visible["next_date"],
                    width=SCALES[scale][2], segments=segments, detections=shading, events=events,
                    kind=series["kind"], categories=series["categories"],
                    raw_count=len(times), displayed_count=len(picked), invalid_count=int((~finite).sum()),
                    downsampled=int(finite.sum()) > len(picked), gap_threshold_seconds=series["gap_ns"]/1e9,
                    ticks=[dict(offset=(t-a)/1e9, label=value.strftime(tick_format)) for t,value in sorted(ticks.items())])
