"""Shared manual/CSV validation, with no silent removal of invalid records."""
import csv
import io

from datasets import iso, timestamp

FIELDS = ("channel", "starttime", "endtime")


def validate_records(rows, dataset, max_records=100000):
    report = dict(filename=None, raw_count=len(rows), valid_count=0, duplicate_count=0,
                  channel_count=0, coverage=None, errors=[], warnings=[], preview=[], records=[])
    if len(rows) > max_records:
        report["errors"].append(dict(line=0, message=f"记录数量超过上限 {max_records}"))
        return report
    seen, channels, starts, ends = set(), set(), [], []
    for index, row in enumerate(rows, 1):
        line = row.get("_line", index)
        item = {key: str(row.get(key, "")).strip() for key in FIELDS}
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
        return dict(filename=filename, raw_count=len(rows), valid_count=0, duplicate_count=0,
                    channel_count=0, coverage=None, errors=[dict(line=0, message=error)],
                    warnings=[], preview=[], records=[])
    report = validate_records(rows, dataset, max_records)
    report["filename"] = filename
    return report
