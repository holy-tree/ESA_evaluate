"""Explicit, request-local Mission configuration and label loading; no telemetry IO."""
import csv
import json
from pathlib import Path

import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent
MISSIONS = ("Mission1", "Mission2", "Mission3")
CATEGORIES = {"both": ["Anomaly", "Rare Event"], "anomaly": ["Anomaly"]}


class DataError(ValueError):
    pass


def timestamp(value):
    """ISO-8601 only; naive timestamps mean UTC, preserve nanoseconds."""
    text = str(value).strip()
    if not text or len(text) < 10 or text[4:5] != "-" or text[7:8] != "-":
        raise ValueError("时间须为 ISO-8601，例如 2024-01-01T00:00:10Z")
    t = pd.Timestamp(text)
    if pd.isna(t):
        raise ValueError("时间不能为空或 NaT")
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.as_unit("ns")


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def read_csv(path):
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise DataError(f"CSV 缺少表头：{path}")
            names = [s.strip().lower() for s in reader.fieldnames]
            if len(set(names)) != len(names):
                raise DataError(f"CSV 存在重复列名：{path}")
            rows = []
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise DataError(f"CSV 列数不一致：{path}，第 {reader.line_num} 行")
                rows.append({k.strip().lower(): v.strip() for k, v in row.items()})
            return names, rows
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"无法读取配置数据 {path}：{exc}") from exc


def load_dataset_root(settings_file=None):
    """Read the real dataset root from YAML; relative values are project-relative."""
    path = Path(settings_file) if settings_file is not None else BASE / "configs" / "settings.yaml"
    if not path.is_absolute():
        path = BASE / path
    try:
        settings = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise DataError(f"无法读取 YAML 配置 {path}：{exc}") from exc
    if not isinstance(settings, dict) or not isinstance(settings.get("dataset_root"), str) or not settings["dataset_root"].strip():
        raise DataError(f"YAML 配置 {path} 的 dataset_root 必须为非空路径字符串")
    root = Path(settings["dataset_root"].strip()).expanduser()
    return (root if root.is_absolute() else BASE / root).resolve()


def load_config(mission, source, version=None, config_dir=None, settings_file=None):
    if mission not in MISSIONS or source not in ("real", "demo"):
        raise DataError("Mission 或数据来源无效")
    try:
        config = json.loads((Path(config_dir or BASE / "configs") / f"{mission}.json").read_text(encoding="utf-8"))
        cfg = dict(config["sources"][source])
        if config["mission_id"] != mission:
            raise DataError("配置的 mission_id 不匹配")
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise DataError(f"Mission 配置不可读取：{exc}") from exc
    if version is not None and version != cfg["version"]:
        raise DataError("数据版本已变化，请重新加载当前 Mission 并校验")
    root = load_dataset_root(settings_file) if source == "real" else BASE
    cfg.update(mission_id=mission, source=source, default_categories=config.get("default_categories", "both"))
    cfg["paths"] = {k: str((root / cfg[k]).resolve()) for k in ("labels", "channels", "anomaly_types") if cfg.get(k)}
    return cfg


def load_dataset(cfg, start=None, end=None, confirmed=False, require_range=True):
    cfg = dict(cfg)
    if start is not None or end is not None:
        if not confirmed:
            raise DataError("请明确勾选确认自定义评测窗口")
        cfg.update(start=start, end=end, custom_range=True)
    if not cfg.get("start") or not cfg.get("end"):
        if require_range:
            raise DataError("缺少固定评测时间范围，请填写开始、结束时间并确认")
        window = None
    else:
        try:
            window = (timestamp(cfg["start"]), timestamp(cfg["end"]))
            if window[0] >= window[1]:
                raise ValueError("评测窗口开始时间必须早于结束时间")
            cfg.update(start=iso(window[0]), end=iso(window[1]))
        except (ValueError, OverflowError) as exc:
            raise DataError(f"评测窗口无效：{exc}") from exc
    if cfg.get("target_channels"):
        channels = cfg["target_channels"]
        if not isinstance(channels, list) or not all(isinstance(c, str) and c.strip() for c in channels):
            raise DataError("target_channels 必须是非空通道名称列表")
    else:
        names, rows = read_csv(cfg["paths"]["channels"])
        if not {"channel", "target"}.issubset(names):
            raise DataError("无法识别 channels.csv：需要 Channel 和 Target；也可在配置中明确填写 target_channels")
        if any(r["target"].lower() not in ("yes", "no", "true", "false", "1", "0") for r in rows):
            raise DataError("channels.csv 中 Target 值无法识别，请明确配置 target_channels")
        channels = [r["channel"] for r in rows if r["target"].lower() in ("yes", "true", "1")]
    channels = list(dict.fromkeys(channels))
    if not channels or any(not c for c in channels):
        raise DataError("目标通道列表为空或无效，不能默认评测所有通道")
    names, rows = read_csv(cfg["paths"]["labels"])
    if not {"id", "channel", "starttime", "endtime"}.issubset(names):
        raise DataError("labels.csv 必须包含 ID,Channel,StartTime,EndTime")
    types = {}
    if "category" not in names:
        if not cfg["paths"].get("anomaly_types"):
            raise DataError("真实标签缺少 Category，请配置 anomaly_types.csv")
        type_names, type_rows = read_csv(cfg["paths"]["anomaly_types"])
        if not {"id", "category"}.issubset(type_names):
            raise DataError("anomaly_types.csv 需要 ID 和 Category")
        for r in type_rows:
            if r["id"] in types and types[r["id"]] != r["category"]:
                raise DataError(f"事件 {r['id']} 类别冲突")
            types[r["id"]] = r["category"]
    labels, categories_by_id, label_warnings, label_errors = [], {}, [], []
    for number, r in enumerate(rows, 2):
        category = r.get("category", types.get(r["id"], ""))
        if not r["id"] or not category:
            raise DataError(f"标签第 {number} 行缺少事件 ID 或 Category，请补充")
        if r["id"] in categories_by_id and categories_by_id[r["id"]] != category:
            raise DataError(f"同一事件 {r['id']} 的 Category 不一致")
        categories_by_id[r["id"]] = category
        try:
            a, b = timestamp(r["starttime"]), timestamp(r["endtime"])
            if a > b:
                # Never guess a repair. A reversed row's entire endpoint hull must
                # be disjoint from a configured window before it can be out of scope.
                message = f"标签第 {number} 行（{r['id']} / {r['channel']}）开始时间晚于结束时间：{iso(a)} → {iso(b)}"
                if window and (max(a, b) < window[0] or min(a, b) > window[1]):
                    label_warnings.append(message + "；两端点包络完全位于本次窗口外，未参与本次评测")
                    continue
                if not require_range:
                    # Metadata views must remain usable so the user can change
                    # an affected default window. This dataset is not scoreable.
                    label_errors.append(message)
                    label_warnings.append(message + "；请修复数据或选择完全不相交的评测窗口；当前范围不可评分")
                    continue
                raise ValueError("开始晚于结束")
        except (ValueError, OverflowError) as exc:
            raise DataError(f"标签第 {number} 行时间无效：{exc}") from exc
        if r["channel"] not in channels:
            continue
        if window:
            if b < window[0] or a > window[1]:
                continue
            a, b = max(a, window[0]), min(b, window[1])
        labels.append(dict(id=r["id"], channel=r["channel"], start=a, end=b, category=category))
    cfg["target_channels"] = channels
    cfg["label_warnings"] = label_warnings
    cfg["label_errors"] = label_errors
    return dict(config=cfg, channels=channels, window=window, labels=labels)
