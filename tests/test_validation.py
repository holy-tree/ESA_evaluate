import pytest

from datasets import timestamp
from validation import parse_csv, validate_records


@pytest.mark.parametrize("header", ["channel,starttime,endtime", "Channel,StartTime,EndTime", " Channel , STARTTIME , endtime "])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_headers_and_bom(demo, header, bom):
    content = bom + (header + "\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n").encode()
    report = parse_csv(content, "test.csv", demo())
    assert not report["errors"]
    assert len(report["records"]) == 1


def test_duplicate_and_extra_id_ignored(demo):
    content = b"Channel,StartTime,EndTime,ID\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z,FAKE\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z,OTHER\n"
    report = parse_csv(content, "test.csv", demo())
    assert report["raw_count"] == report["valid_count"] == 2
    assert report["duplicate_count"] == 1
    assert len(report["records"]) == 1
    assert "id" not in report["records"][0]


@pytest.mark.parametrize("row,message", [
    ("bad,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z", "通道"),
    ("channel_1,,2024-01-01T00:00:18Z", "不能为空"),
    ("channel_1,broken,2024-01-01T00:00:18Z", "时间格式"),
    ("channel_1,2024-01-01T00:00:18Z,2024-01-01T00:00:12Z", "晚于"),
    ("channel_1,2023-01-01T00:00:12Z,2024-01-01T00:00:18Z", "超出"),
])
def test_invalid_record_line_numbers(demo, row, message):
    report = parse_csv(("channel,starttime,endtime\n" + row).encode(), "test.csv", demo())
    assert any(e["line"] == 2 and message in e["message"] for e in report["errors"])


@pytest.mark.parametrize("content", [b"x,y,z\n", b"channel,CHANNEL,starttime,endtime\n", b"\xff\xfe", b"channel,starttime,endtime\na,b\n"])
def test_bad_file(demo, content):
    assert parse_csv(content, "bad.csv", demo())["errors"]


def test_column_count_error_keeps_other_records(demo):
    content = b"channel,starttime,endtime\na,b\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n"
    report = parse_csv(content, "columns.csv", demo())
    assert report["raw_count"] == 2 and report["valid_count"] == 1
    assert len(report["preview"]) == 2
    assert any(e["line"] == 2 and "字段数量" in e["message"] for e in report["errors"])


def test_empty_valid_header(demo):
    report = parse_csv(b"channel,starttime,endtime\n", "empty.csv", demo())
    assert not report["errors"]
    assert report["raw_count"] == 0


def test_limits(demo):
    content = b"channel,starttime,endtime\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n"
    assert "大小上限" in parse_csv(content, "a.csv", demo(), max_bytes=10)["errors"][0]["message"]
    assert "记录数量" in parse_csv(content + content.splitlines(keepends=True)[1], "a.csv", demo(), max_records=1)["errors"][0]["message"]


def test_timezone_nanosecond_and_point(demo):
    assert timestamp("2024-01-01T08:00:12+08:00") == timestamp("2024-01-01T00:00:12")
    assert timestamp("2024-01-01T00:00:12.123456789Z").nanosecond == 789
    report = validate_records([dict(channel="channel_1", starttime="2024-01-01T00:00:12Z", endtime="2024-01-01T00:00:12Z")], demo())
    assert not report["errors"]
