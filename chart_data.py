"""Display-only continuous windows, bucket statistics, smoothing and navigation."""
from collections import defaultdict

import numpy as np
import pandas as pd
import portion as P

from datasets import CATEGORIES, DataError, iso, read_csv, timestamp
from telemetry import event_annotations, select_extrema, select_states

MODES = {"auto", "raw", "aggregate", "moving", "kernel"}


def navigation_range(cfg, fallback):
    if cfg.get("start") and cfg.get("end"):
        bounds = (timestamp(cfg["start"]), timestamp(cfg["end"]))
        if bounds[0] >= bounds[1]:
            raise DataError("数据集观测范围配置无效")
    else:
        bounds = fallback
    return [iso(t) for t in bounds]


def continuous_window(domain, start, end):
    try:
        a, b = timestamp(start), timestamp(end)
    except (ValueError, OverflowError) as exc:
        raise DataError(f"连续时间窗口无效：{exc}") from exc
    if a >= b:
        raise DataError("图表窗口开始时间必须早于结束时间")
    if a < domain[0] or b > domain[1]:
        raise DataError("图表窗口超出数据集完整观测范围")
    return a, b


def chart_ticks(a, b):
    seconds = (b-a).total_seconds()
    choices = [(120, "10s", "%H:%M:%S", "秒"), (7200, "10min", "%m-%d %H:%M", "分"),
               (172800, "6h", "%m-%d %H:%M", "时"), (1209600, "2D", "%m-%d", "日"),
               (8035200, "7D", "%m-%d", "周"), (63244800, "3MS", "%Y-%m", "月"),
               (float("inf"), "YS", "%Y", "年")]
    _, frequency, fmt, unit = next(row for row in choices if seconds <= row[0])
    if frequency in ("3MS", "YS"):
        anchor = a.normalize().replace(day=1)
        if frequency == "YS":
            anchor = anchor.replace(month=1)
    else:
        anchor = a.floor("D")
    ticks = {a.value: a, b.value: b}
    for t in pd.date_range(anchor, b, freq=frequency):
        if a <= t <= b:
            ticks[t.value] = t
    return unit, [dict(offset=(value-a.value)/1e9, label=t.strftime(fmt)) for value,t in sorted(ticks.items())]


def prediction_checkpoints(predictions, channels):
    """Navigation-only unions; preserve source rows/channels, never GT identities."""
    rows = sorted((timestamp(r["start"]).value, timestamp(r["end"]).value, r["channel"], i)
                  for i,r in enumerate(predictions) if r["channel"] in channels)
    merged = []
    for start, end, channel, index in rows:
        if merged and start <= merged[-1][1]:
            item = merged[-1]
            item[1] = max(item[1], end)
            item[2].add(channel)
            item[3].append(index)
        else:
            merged.append([start, end, {channel}, [index]])
    return [dict(id=f"prediction-{i+1}", start=iso(pd.Timestamp(a, tz="UTC")), end=iso(pd.Timestamp(b, tz="UTC")),
                 channels=sorted(names), prediction_indices=indices, point=a == b)
            for i,(a,b,names,indices) in enumerate(merged)]


def smooth_buckets(bins, mode, bandwidth):
    """Time-window display smoothing of bucket means, never across data breaks."""
    result = np.array([row["median"] for row in bins], dtype=float)
    if mode not in ("moving", "kernel") or not bins:
        return result
    groups = defaultdict(list)
    for i,row in enumerate(bins):
        groups[row["block"]].append(i)
    for positions in groups.values():
        times = np.array([bins[i]["offset"] for i in positions])
        means = np.array([bins[i]["mean"] for i in positions])
        counts = np.array([bins[i]["count"] for i in positions], dtype=float)
        if mode == "moving":
            starts = np.searchsorted(times, times-bandwidth/2, side="left")
            stops = np.searchsorted(times, times+bandwidth/2, side="right")
            weights = np.r_[0, np.cumsum(counts)]
            sums = np.r_[0, np.cumsum(means*counts)]
            result[positions] = (sums[stops]-sums[starts])/(weights[stops]-weights[starts])
        else:
            for local, position in enumerate(positions):
                first, last = np.searchsorted(times, [times[local]-3*bandwidth, times[local]+3*bandwidth], side="left")
                last = max(last, local+1)
                weight = np.exp(-.5*((times[first:last]-times[local])/bandwidth)**2)*counts[first:last]
                result[position] = np.sum(means[first:last]*weight)/weight.sum()
    return result


def bucket_statistics(times, values, finite, groups, a, b, count):
    """Time buckets split at every original continuity block, including NaNs."""
    edges = np.array([a + (b-a)*i//count for i in range(count+1)], dtype=np.int64)
    cuts = np.searchsorted(times, edges, side="left")
    cuts[-1] = len(times)  # the display includes a sample exactly at the right edge
    bins = []
    for first,last in zip(cuts[:-1], cuts[1:]):
        valid = np.flatnonzero(finite[first:last]) + first
        if not len(valid):
            continue
        splits = np.r_[0, np.flatnonzero(np.diff(groups[valid]) != 0)+1, len(valid)]
        for lo,hi in zip(splits[:-1], splits[1:]):
            selected = valid[lo:hi]
            v = values[selected]
            q05, median, q95 = np.percentile(v, [5, 50, 95])
            start, end = int(times[selected[0]]), int(times[selected[-1]])
            bins.append(dict(start=(start-a)/1e9, end=(end-a)/1e9, offset=((start-a)+(end-a))/2e9,
                start_time=iso(pd.Timestamp(start, tz="UTC")), end_time=iso(pd.Timestamp(end, tz="UTC")),
                count=len(selected), min=float(v.min()), max=float(v.max()), p05=float(q05), p95=float(q95),
                median=float(median), mean=float(v.mean(dtype=np.float64)), block=int(groups[selected[0]])))
    return bins


def display_cadence(times, fallback):
    """Recognize mixed sampling regimes; isolated huge jumps are not a cadence."""
    if len(times) < 6:
        return fallback, fallback/10e9
    positions = np.linspace(0, len(times)-2, min(10000, len(times)-1), dtype=int)
    steps = times[positions+1]-times[positions]
    positive = steps[steps > 0]
    if not len(positive):
        return fallback, fallback/10e9
    median = float(np.median(positive))
    rounded = np.rint(positive/1_000_000).astype(np.int64)
    names, counts = np.unique(rounded, return_counts=True)
    common = names[counts >= max(3, len(positive)*.001)]
    typical = positive[np.isin(rounded, common)]
    if not len(typical):
        typical = positive[positive <= median*1000]
    # Frequency, rather than a ratio to the fastest rate, recognizes persistent
    # slow-rate regimes (e.g. 90 s alongside 0.375 s). Rare long jumps are not
    # used as evidence of the normal display cadence.
    upper = max(median, float(np.percentile(typical,99)))
    return max(1_000_000_000, int(upper*10)), median/1e9


def channel_view(store, dataset, channel, a, b, predictions, category, mode, bandwidth, budget, bins_count):
    series = store.series(dataset, channel)
    left = int(np.searchsorted(series["times"], a.value, side="left"))
    right = int(np.searchsorted(series["times"], b.value, side="right"))
    times = series["times"][left:right]
    values = np.asarray(series["values"][left:right])
    gap_ns, cadence_seconds = display_cadence(times, series["gap_ns"])
    finite = np.isfinite(values)
    breaks = np.zeros(len(times), dtype=bool)
    if len(times) > 1:
        breaks[1:] = (np.diff(times) > gap_ns) | ~finite[1:] | ~finite[:-1]
    groups = np.cumsum(breaks, dtype=np.int32)
    picked = select_states(values, budget, breaks) if series["kind"] == "categorical" else select_extrema(values, budget)
    support = []
    borders = np.r_[0, np.flatnonzero(breaks), len(times)]
    for first,last in zip(borders[:-1], borders[1:]):
        if first < last and finite[first]:
            support.append(P.closed(int(times[first]), int(times[last-1])))
    segments, current, prior = [], [], None
    for i in picked:
        if prior is not None and groups[i] != prior:
            segments.append(current)
            current = []
        current.append([(int(times[i])-a.value)/1e9, float(values[i]), iso(pd.Timestamp(int(times[i]), tz="UTC")), left+int(i)])
        prior = groups[i]
    if current:
        segments.append(current)
    events, memberships = event_annotations(dataset, channel, a.value, b.value, support, times[picked], category)
    for point, ids in zip((p for s in segments for p in s), memberships):
        point.append(ids)
    effective = "states" if series["kind"] == "categorical" else mode
    if effective == "auto":
        effective = "aggregate" if finite.sum() > bins_count*2 else "raw"
    bins, trend = [], []
    if effective not in ("raw", "states"):
        bins = bucket_statistics(times, values, finite, groups, a.value, b.value, bins_count)
        smoothed = smooth_buckets(bins, effective, bandwidth)
        previous, current = None, []
        for i,row in enumerate(bins):
            if previous is not None and row["block"] != previous:
                trend.append(current)
                current = []
            current.append([row["offset"], float(smoothed[i]), i])
            previous = row["block"]
        if current:
            trend.append(current)
    pred_intervals = []
    for row in predictions:
        if row["channel"] == channel:
            start, end = timestamp(row["start"]).value, timestamp(row["end"]).value
            # Display boundaries retain the score's prediction endpoint convention.
            pred_intervals.append(P.closed(start, end) if start == end or end == dataset.get("evaluation_window", dataset["window"])[1].value else P.closedopen(start, end))
    overlap = P.Interval(*pred_intervals) & P.Interval(*support) & P.closed(a.value, b.value)
    detections = [dict(start=(i.lower-a.value)/1e9, end=(i.upper-a.value)/1e9, point=i.lower == i.upper,
        right_closed=i.right == P.CLOSED, start_time=iso(pd.Timestamp(i.lower, tz="UTC")), end_time=iso(pd.Timestamp(i.upper, tz="UTC"))) for i in overlap]
    namespace = f"{dataset['config']['mission_id']}|{dataset['config']['version']}|{dataset['config']['source']}|{channel}"
    rank = dataset["channels"].index(channel)
    color = f"hsl({(218 + rank*137.507764)%360:.3f}, 76%, 35%)"
    return dict(channel=channel, key=namespace, color=color, kind=series["kind"], categories=series["categories"],
        segments=segments, events=events, detections=detections, bins=bins, trend_segments=trend,
        mode=effective, raw_count=len(times), displayed_count=sum(map(len, trend)) if bins else len(picked),
        invalid_count=int((~finite).sum()), downsampled=int(finite.sum()) > len(picked),
        reference=series["reference"], cadence_seconds=cadence_seconds,
        gap_threshold_seconds=gap_ns/1e9)


def build_view(store, dataset, channels, start, end, predictions=(), mode="auto", bandwidth=None,
               pixel_width=1000, max_points=8000, category="both", include_checkpoints=False):
    if not isinstance(channels, list) or not channels or any(not isinstance(c, str) or c not in dataset["channels"] for c in channels):
        raise DataError("请至少选择一个当前 Mission 的目标通道")
    channels = list(dict.fromkeys(channels))
    if mode not in MODES or category not in CATEGORIES:
        raise DataError("显示处理方式或事件类别无效")
    a,b = continuous_window(dataset["window"], start, end)
    span = (b.value-a.value)/1e9
    try:
        width = max(400, min(2400, int(pixel_width)))
        smooth_seconds = span/50 if bandwidth in (None, "") else float(bandwidth)
        if not np.isfinite(smooth_seconds) or smooth_seconds <= 0:
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataError("绘图宽度或平滑窗口无效，平滑窗口须为正秒数") from exc
    budget = max(16, min(max_points, width*4//len(channels)))
    bins_count = max(8, min(width//3, budget//4))
    result = [channel_view(store, dataset, c, a, b, predictions, category, mode, smooth_seconds, budget, bins_count) for c in channels]
    # Legend navigation uses the true event start across selected channels, not
    # the first point/fragment surviving the current viewport or pixel budget.
    event_starts = {}
    for row in dataset.get("labels", []):
        if row["channel"] in channels:
            event_starts[row["id"]] = min(event_starts.get(row["id"], row["start"]), row["start"])
    for item in result:
        for event in item["events"]:
            event["event_start"] = iso(event_starts[event["id"]])
    unit, ticks = chart_ticks(a,b)
    units = {}
    if dataset["config"]["paths"].get("channels"):
        _, rows = read_csv(dataset["config"]["paths"]["channels"])
        units = {row["channel"]: row.get("physical unit", row.get("unit", "未标明")) or "未标明" for row in rows}
    for item in result:
        item["unit"] = units.get(item["channel"], "未标明")
    response = dict(mission=dataset["config"]["mission_id"], version=dataset["config"]["version"],
        source=dataset["config"]["source"], timezone="UTC", window_start=iso(a), window_end=iso(b), span_seconds=span,
        navigation_range=[iso(t) for t in dataset["window"]],
        evaluation_range=[iso(t) for t in dataset.get("evaluation_window", dataset["window"])],
        channels=result, ticks=ticks, tick_unit=unit, bandwidth_seconds=smooth_seconds, mode=mode,
        normalization="绘图参考：全通道时间范围内至多 20000 个等位置采样的 median/IQR（IQR 为零时使用标准差）；不参与训练和评分")
    if include_checkpoints:
        response["checkpoints"] = prediction_checkpoints(predictions, channels)
    return response
