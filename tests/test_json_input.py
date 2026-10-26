import html
import io
import json
import re

import pytest

from app import create_app
from datasets import BASE, MISSIONS, load_config, load_dataset, read_csv, timestamp
from validation import parse_json
from test_app import exported, extract_token, fields, post_csv


def record(**changes):
    result = dict(channel="channel_1", starttime="2024-01-01T00:00:12Z", endtime="2024-01-01T00:00:18Z")
    result.update(changes)
    return result


def encode(value, bom=False):
    return (b"\xef\xbb\xbf" if bom else b"") + json.dumps(value, ensure_ascii=False).encode("utf-8")


def post_json(client, mission="Mission1", content=None, **extra):
    if content is None:
        _, rows = read_csv(BASE / "demo_data" / mission / "predictions.csv")
        content = encode(rows)
    data = fields(mission, "json")
    data.update(extra)
    data["prediction_json"] = (io.BytesIO(content), "predictions.json")
    return client.post("/", data=data)


def upload_token(response):
    token = re.search(r'id="upload-token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
    assert token
    return html.unescape(token[1])


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("names", [list(record()), ["Channel", "StartTime", "EndTime"], [" CHANNEL ", " StartTime ", " endtime "]])
def test_json_shapes_field_names_and_encoding(demo, wrapped, bom, names):
    rows = [dict(zip(names, record().values()))]
    payload = {"predictions": rows} if wrapped else rows
    report = parse_json(encode(payload,bom), "a.json", demo())
    assert not report["errors"]
    assert report["input_format"] == "json"
    assert report["raw_count"] == report["valid_count"] == 1
    assert report["records"][0]["start"] == timestamp(record()["starttime"])


@pytest.mark.parametrize("payload", [{}, {"predictions": {}}, {"other": []}, None, 42, "[]"])
def test_json_invalid_top_level(demo, payload):
    report = parse_json(encode(payload), "bad.json", demo())
    assert report["errors"] and report["errors"][0]["line"] == 0


@pytest.mark.parametrize("payload", [[None], [42], [[]]])
def test_json_non_object_records_have_record_positions(demo, payload):
    report = parse_json(encode(payload), "bad.json", demo())
    assert any(e["line"] == 1 and "必须是对象" in e["message"] for e in report["errors"])


@pytest.mark.parametrize("row,message", [
    (record(channel="unknown"), "目标通道"), (record(starttime="bad"), "时间格式"),
    (record(starttime="2024-01-01T00:00:25Z"), "晚于"),
    (record(endtime="2030-01-01T00:00:00Z"), "超出"),
    (record(channel=None), "不能为空"), (record(starttime=12345), "须为字符串"),
    (record(endtime=[]), "须为字符串"), (dict(channel="channel_1"), "缺少必需字段")])
def test_json_record_validation_errors(demo, row, message):
    report = parse_json(encode([record(),row]), "bad.json", demo())
    assert report["raw_count"] == 2 and report["valid_count"] == 1
    assert any(e["line"] == 2 and message in e["message"] for e in report["errors"])


def test_json_duplicate_fields_and_malformed_syntax(demo):
    duplicates = b'[{"channel":"channel_1"," Channel ":"channel_1","starttime":"2024-01-01T00:00:12Z","endtime":"2024-01-01T00:00:18Z"}]'
    assert any(e["line"] == 1 and "重复" in e["message"] for e in parse_json(duplicates,"d.json",demo())["errors"])
    root_dupe = b'{"predictions": [], "Predictions": []}'
    assert "顶层字段名重复" in parse_json(root_dupe,"d.json",demo())["errors"][0]["message"]
    syntax = parse_json(b'[\n {"channel":}\n]', "syntax.json", demo())
    assert "第 2 行" in syntax["errors"][0]["message"] and "列" in syntax["errors"][0]["message"]
    for raw in (b"[NaN]", b"[Infinity]", b"[1,]", b"\xff\xfe"):
        assert parse_json(raw,"syntax.json",demo())["errors"]


def test_json_duplicates_extra_id_and_untrusted_record_number(demo):
    rows = [record(ID="fake", _line=900, _error="fake"), record(ID="another")]
    report = parse_json(encode(rows), "duplicates.json", demo())
    assert not report["errors"]
    assert report["valid_count"] == 2 and report["duplicate_count"] == 1
    assert len(report["records"]) == 1
    assert report["preview"][0]["line"] == 1
    assert "id" not in report["records"][0]
    assert report["preview"][0]["errors"] == []


def test_json_size_and_record_limits(demo):
    content = encode([record(),record()])
    assert "大小上限" in parse_json(content,"a.json",demo(),max_bytes=10)["errors"][0]["message"]
    report = parse_json(content,"a.json",demo(),max_records=1)
    assert report["raw_count"] == 2
    assert "记录数量" in report["errors"][0]["message"]


@pytest.mark.parametrize("mission", MISSIONS)
def test_three_mission_json_preview_score_export_and_csv_equivalence(client, mission):
    page = client.get("/", query_string=dict(mission=mission,source="demo",mode="json"))
    assert page.status_code == 200 and 'value="json" checked' in page.get_data(as_text=True)
    csv_result = exported(client,post_csv(client,mission))
    response = post_json(client,mission)
    result = exported(client,response)
    assert result["metrics"] == csv_result["metrics"]
    assert result["channel_results"] == csv_result["channel_results"]
    assert result["input_mode"] == result["input_summary"]["input_format"] == "json"
    assert "JSON 预览与校验" in response.get_data(as_text=True)
    preview = post_json(client,mission,action="preview")
    assert 'id="evaluation-result"' not in preview.get_data(as_text=True)
    scored = client.post("/", data=dict(fields(mission,"json"),upload_token=upload_token(preview)))
    assert exported(client,scored)["metrics"] == result["metrics"]
    events = client.post("/download/events",data=dict(token=extract_token(response,"events")))
    assert events.status_code == 200 and mission in events.get_data(as_text=True)
    for kind in ("template", "predictions"):
        example = client.get(f"/demo/{mission}/{kind}.json")
        assert example.status_code == 200 and "DEMO-" in example.headers["Content-Disposition"]
        assert not parse_json(example.data,"example.json",load_dataset(load_config(mission,"demo")))["errors"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_json_empty_predictions_and_point_events(client, wrapped):
    value = {"predictions": []} if wrapped else []
    result = exported(client,post_json(client,content=encode(value)))
    assert result["metrics"]["f0_5"] == 0
    assert result["metrics"]["detected_count"] == 0
    point = exported(client,post_json(client,content=encode([record(endtime=record()["starttime"])])))
    assert point["metrics"]["detected_count"] == 1


def test_json_scoring_uses_records_beyond_preview_limit(client):
    rows = []
    for i in range(100):
        time = f"2024-01-01T00:01:15.{i:09d}Z"
        rows.append(record(starttime=time,endtime=time))
    rows.append(record())
    preview = post_json(client,content=encode(rows),action="preview")
    result = exported(client,client.post("/",data=dict(fields(mode="json"),upload_token=upload_token(preview))))
    assert result["input_summary"]["raw_count"] == 101
    assert result["metrics"]["detected_count"] == 1 and result["metrics"]["false_positive_count"] == 100


def test_json_invalid_record_blocks_score_and_error_download(client):
    response = post_json(client,content=encode([record(),record(channel="bad")]))
    text = response.get_data(as_text=True)
    assert "JSON 记录 2" in text and 'id="evaluation-result"' not in text
    errors = client.post("/download/errors",data=dict(token=extract_token(response,"errors")))
    assert "line_or_record,message" in errors.get_data(as_text=True)
    assert "2,通道" in errors.get_data(as_text=True)


def test_json_upload_does_not_mix_manual_or_csv_inputs(client):
    response = post_json(client,channel="bad",starttime="bad",endtime="bad",
                         prediction_csv=(io.BytesIO(b"broken"),"bad.csv"))
    assert exported(client,response)["metrics"] == exported(client,post_json(client))["metrics"]


@pytest.mark.parametrize("original,new", [("json","csv"),("csv","json")])
def test_format_switch_cannot_reuse_preview(client, original, new):
    preview = post_json(client,action="preview") if original == "json" else post_csv(client,action="preview")
    response = client.post("/",data=dict(fields(mode=new),upload_token=upload_token(preview)))
    assert "输入格式已变化" in response.get_data(as_text=True)
    assert 'id="evaluation-result"' not in response.get_data(as_text=True)


def test_json_mission_switch_revalidates_and_limits_apply_on_web(client):
    preview = post_json(client,action="preview")
    response = client.post("/",data=dict(fields("Mission2","json"),upload_token=upload_token(preview)))
    assert "旧预览失效" in response.get_data(as_text=True)
    small = create_app(dict(TESTING=True,MAX_CSV_BYTES=20)).test_client()
    assert "JSON 文件超过大小上限" in post_json(small).get_data(as_text=True)
    limited = create_app(dict(TESTING=True,MAX_RECORDS=1)).test_client()
    assert "记录数量超过上限" in post_json(limited).get_data(as_text=True)
