"""Shared manual/CSV/JSON validation; invalid records always block scoring."""
import csv
import io
import json

from datasets import iso, timestamp

FIELDS = ("channel", "starttime", "endtime")


def validate_records(rows, dataset, max_records=100000):
    report = dict(filename=None, input_format="manual", raw_count=len(rows), valid_count=0, duplicate_count=0,
                  channel_count=0, coverage=None, errors=[], warnings=[], preview=[], records=[])
    if len(rows) > max_records:
        report["errors"].append(dict(line=0, message=f"记录数量超过上限 {max_records}"))
        return report
    seen, channels, starts, ends = set(), set(), [], []
    for index, row in enumerate(rows, 1):
        line = row.get("_line", index)
        item = {key: "" if row.get(key) is None else str(row.get(key, "")).strip() for key in FIELDS}
        errors = [row["_error"]] if row.get("_error") else []
        if any(not item[k] for k in FIELDS):
            errors.append("必需字段不能为空（channel,starttime,endtime）")
        if item["channel"] not in dataset["channels"]:
            errors.append("通道不属于当前 Mission 的目标通道")
        a = b = None
        try:
            a, b = timestamp(item["starttime"]), timestamp(item["endtime"])
            if a > b:
                errors.append("开始时间晚于结束时间")
            if a < dataset["window"][0] or b > dataset["window"][1]:
                errors.append("预测超出固定评测时间范围")
        except (ValueError, OverflowError) as exc:
            errors.append(f"时间格式非法：{exc}")
        report["errors"].extend(dict(line=line, message=e) for e in errors)
        if len(report["preview"]) < 100:
            report["preview"].append(dict(item, line=line, errors=errors))
        if errors:
            continue
        report["valid_count"] += 1
        key = (item["channel"], a.value, b.value)
        if key in seen:
            report["duplicate_count"] += 1
            continue
        seen.add(key)
        channels.add(item["channel"])
        starts.append(a)
        ends.append(b)
        report["records"].append(dict(channel=item["channel"], start=a, end=b))
    report["channel_count"] = len(channels)
    if starts:
        report["coverage"] = [iso(min(starts)), iso(max(ends))]
    if report["duplicate_count"]:
        report["warnings"].append(f"已去除 {report['duplicate_count']} 条完全重复记录；有效记录数包含重复行。")
    return report


def parse_csv(content, filename, dataset, max_bytes=5 * 1024 * 1024, max_records=100000):
    error = None
    rows = []
    if len(content) > max_bytes:
        error = f"CSV 文件超过大小上限 {max_bytes} 字节"
    else:
        try:
            text = content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(reader, [])
            header = [s.strip().lower() for s in header]
            if not set(FIELDS).issubset(header):
                raise ValueError("缺少必需列 channel,starttime,endtime")
            if len(set(header)) != len(header):
                raise ValueError("字段名去空格并忽略大小写后重复")
            while True:
                line = reader.line_num + 1
                values = next(reader, None)
                if values is None:
                    break
                item = dict(zip(header, values), _line=line)
                if len(values) != len(header):
                    item["_error"] = "字段数量与 CSV 表头不一致"
                rows.append(item)
                if len(rows) > max_records:
                    raise ValueError(f"记录数量超过上限 {max_records}")
        except (UnicodeError, csv.Error, ValueError) as exc:
            error = f"CSV 解析失败：{exc}（支持 UTF-8 / UTF-8 BOM）"
    if error:
        return dict(filename=filename, input_format="csv", raw_count=len(rows), valid_count=0, duplicate_count=0,
                    channel_count=0, coverage=None, errors=[dict(line=0, message=error)],
                    warnings=[], preview=[], records=[])
    report = validate_records(rows, dataset, max_records)
    report["filename"] = filename
    report["input_format"] = "csv"
    return report


class _JSONObject(dict):
    """Normalize keys while detecting exact and case/whitespace duplicates."""
    def __init__(self, pairs):
        super().__init__()
        self.duplicate_keys = []
        for key,value in pairs:
            key = key.strip().lower()
            if key in self:
                self.duplicate_keys.append(key)
            self[key] = value


def parse_json(content, filename, dataset, max_bytes=5 * 1024 * 1024, max_records=100000):
    def failure(message):
        return dict(filename=filename, input_format="json", raw_count=0, valid_count=0, duplicate_count=0,
                    channel_count=0, coverage=None, errors=[dict(line=0, message=message)],
                    warnings=[], preview=[], records=[])

    if len(content) > max_bytes:
        return failure(f"JSON 文件超过大小上限 {max_bytes} 字节")
    def reject_constant(value):
        raise ValueError(f"JSON 不允许使用 {value} 常量")
    try:
        document = json.loads(content.decode("utf-8-sig"), object_pairs_hook=_JSONObject, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        return failure(f"JSON 解析失败：第 {exc.lineno} 行、第 {exc.colno} 列，{exc.msg}")
    except (UnicodeError, ValueError, RecursionError) as exc:
        return failure(f"JSON 解析失败：{exc}（支持 UTF-8 / UTF-8 BOM）")
    if isinstance(document, dict):
        if document.duplicate_keys:
            return failure("JSON 顶层字段名重复：" + ", ".join(document.duplicate_keys))
        if "predictions" not in document:
            return failure('JSON 顶层必须是预测对象数组，或 {"predictions": [...]}')
        document = document["predictions"]
    if not isinstance(document, list):
        return failure('JSON 预测记录必须是数组，例如 [] 或 [{"channel": ..., "starttime": ..., "endtime": ...}]')
    if len(document) > max_records:
        report = validate_records(document, dataset, max_records)
        report.update(filename=filename, input_format="json")
        return report
    rows = []
    for index,row in enumerate(document, 1):
        errors = []
        if not isinstance(row, dict):
            row = {}
            errors.append("每条 JSON 预测记录必须是对象")
        duplicates = getattr(row, "duplicate_keys", [])
        if duplicates:
            errors.append("字段名去空格并忽略大小写后重复：" + ", ".join(duplicates))
        missing = [key for key in FIELDS if key not in row]
        if missing:
            errors.append("缺少必需字段：" + ", ".join(missing))
        invalid_types = [key for key in FIELDS if key in row and not isinstance(row[key], str)]
        if invalid_types:
            errors.append("必需字段须为字符串：" + ", ".join(invalid_types))
        # Do not trust uploaded ID, _line, _error, Mission or other extra fields.
        rows.append(dict({key: row.get(key, "") for key in FIELDS}, _line=index, _error="；".join(errors)))
    report = validate_records(rows, dataset, max_records)
    report.update(filename=filename, input_format="json")
    return report
