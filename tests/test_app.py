import html
import csv
import io
import json
import re

import pytest

from app import create_app
from datasets import BASE, MISSIONS, DataError, iso, load_config, load_dataset, read_csv
from itsdangerous import URLSafeTimedSerializer


def fields(mission="Mission1", mode="csv"):
    return dict(mission=mission, source="demo", version="demo-1.0", mode=mode, action="evaluate", user_id="test-user")


def extract_token(response, action):
    text = response.get_data(as_text=True)
    match = re.search(r'action="/download/' + action + r'"[^>]*>\s*<input[^>]*value="([^"]+)"', text)
    assert match, text[:2000]
    return html.unescape(match[1])


def exported(client, response):
    """Inspect the signed server snapshot without adding a JSON download endpoint."""
    token = extract_token(response, "csv")
    signer = URLSafeTimedSerializer(client.application.config["SECRET_KEY"], salt="esa-evaluation-v1")
    envelope = signer.loads(token, max_age=client.application.config["TOKEN_MAX_AGE"])
    assert envelope["kind"] == "result"
    return envelope["payload"]


def result_csv_table(client, response):
    download = client.post("/download/csv", data=dict(token=extract_token(response, "csv")))
    assert download.status_code == 200 and download.data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(download.get_data(as_text=True).lstrip("\ufeff"))))
    start = next(i for i, row in enumerate(rows) if row[:2] == ["范围", "通道"])
    return [dict(zip(rows[start], row)) for row in rows[start+1:]]


def post_csv(client, mission="Mission1", content=None, **extra):
    if content is None:
        content = (BASE / "demo_data" / mission / "predictions.csv").read_bytes()
    return client.post("/", data=dict(fields(mission), prediction_csv=(io.BytesIO(content), "prediction.csv"), **extra))


@pytest.mark.parametrize("mission", MISSIONS)
def test_complete_manual_csv_preview_exports(client, mission):
    page = client.get("/", query_string=dict(mission=mission, source="demo"))
    assert page.status_code == 200
    assert mission in page.get_data(as_text=True)
    csv_result = post_csv(client, mission)
    assert csv_result.status_code == 200
    csv_json = exported(client, csv_result)
    assert csv_json["config"]["mission_id"] == mission
    assert csv_json["status"] == "ok"
    assert "演示" in csv_result.get_data(as_text=True)
    ds = load_dataset(load_config(mission, "demo"))
    assert csv_json["config"]["aggregation"] == "channel_macro_mean"
    assert [row["channel"] for row in csv_json["channel_results"]] == ds["channels"]
    assert 'id="channel-scores"' in csv_result.get_data(as_text=True)
    channel_rows = result_csv_table(client, csv_result)
    assert channel_rows[0]["范围"] == "总体"
    assert [row["通道"] for row in channel_rows[1:]] == ds["channels"]
    _, rows = read_csv(BASE / "demo_data" / mission / "predictions.csv")
    manual = client.post("/", data=dict(fields(mission, "manual"),
        channel=[r["channel"] for r in rows], starttime=[r["starttime"] for r in rows], endtime=[r["endtime"] for r in rows]))
    assert exported(client, manual)["metrics"] == csv_json["metrics"]
    assert exported(client, manual)["channel_results"] == csv_json["channel_results"]
    assert all("F0.5" in row and "误报区间" in row for row in channel_rows)
    example = client.get("/example", query_string=dict(mission=mission, source="demo", version="demo-1.0"))
    assert example.get_json()["rows"] == rows
    for kind in ("template", "predictions"):
        downloaded = client.get(f"/demo/{mission}/{kind}.csv")
        assert downloaded.status_code == 200
        assert "DEMO-" in downloaded.headers["Content-Disposition"]
    # Preview retains all data and can score without re-selecting the browser file.
    preview = post_csv(client, mission, action="preview")
    assert 'id="evaluation-result"' not in preview.get_data(as_text=True)
    token = re.search(r'id="upload-token"[^>]*value="([^"]+)"', preview.get_data(as_text=True))[1]
    scored = client.post("/", data=dict(fields(mission), upload_token=html.unescape(token)))
    assert exported(client, scored)["metrics"] == csv_json["metrics"]


def test_mission_sequence_and_same_ids_channels(client):
    results = [exported(client, post_csv(client, m)) for m in (*MISSIONS, "Mission1")]
    assert results[0]["metrics"] == results[-1]["metrics"]
    assert results[0]["metrics"] != results[1]["metrics"]
    for result in results:
        assert result["events"][0]["id"] == "id_1"
        assert "channel_1" in result["config"]["target_channels"]
    assert results[1]["config"]["start"] != results[2]["config"]["start"]


@pytest.mark.parametrize("change", [dict(mission="Mission2"),
                                    dict(range_start="2024-01-01T00:00:00Z", range_end="2024-01-01T00:01:30Z", confirm_range="yes")])
def test_stale_preview_rejected(client, change):
    preview = post_csv(client, action="preview")
    token = re.search(r'id="upload-token"[^>]*value="([^"]+)"', preview.get_data(as_text=True))[1]
    data = fields()
    data.update(change, upload_token=html.unescape(token))
    response = client.post("/", data=data)
    assert "旧预览失效" in response.get_data(as_text=True)
    assert 'id="evaluation-result"' not in response.get_data(as_text=True)


@pytest.mark.parametrize("supplied_category", [None, "both", "anomaly", "unknown"])
def test_web_always_scores_three_categories_without_a_selector(client, supplied_category):
    page = client.get("/?mission=Mission3&source=demo&category=anomaly").get_data(as_text=True)
    assert '<select id="category"' not in page
    assert 'name="category"' not in page
    assert "Anomaly + Rare Event + Communication Gap" in page
    data = dict(fields("Mission3", "manual"), channel="channel_1",
                starttime="2024-03-01T00:01:02Z", endtime="2024-03-01T00:01:08Z")
    if supplied_category is not None:
        data["category"] = supplied_category
    response = client.post("/", data=data)
    result = exported(client, response)
    assert result["config"]["category_mode"] == "all"
    assert result["config"]["selected_categories"] == ["Anomaly", "Rare Event", "Communication Gap"]
    assert result["metrics"]["event_count"] == 4
    assert result["metrics"]["detected_count"] == 1
    assert next(e for e in result["events"] if e["id"] == "gap")["detected"]
    from test_telemetry import request_series
    chart = request_series(client, response, "channel_1", "2024-03-01", scored=True).get_json()
    assert next(e for e in chart["events"] if e["id"] == "gap")["selected"]


def test_previous_protocol_signed_results_expire(client):
    from itsdangerous import URLSafeTimedSerializer
    signer = URLSafeTimedSerializer(client.application.config["SECRET_KEY"], salt="esa-evaluation-v1")
    token = signer.dumps(dict(kind="result", protocol="channel-macro-corrected-eventwise-f0.5/v1", payload={}))
    response = client.post("/download/csv", data=dict(token=token))
    assert response.status_code == 400
    assert "评分协议已变化" in response.get_data(as_text=True)


def test_all_records_not_preview_only(client):
    # 100 distinct false-positive points, then an anomaly hit only at record 101.
    content = "channel,starttime,endtime\n"
    for i in range(100):
        t = f"2024-01-01T00:01:15.{i:09d}Z"
        content += f"channel_1,{t},{t}\n"
    content += "channel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n"
    result = exported(client, post_csv(client, content=content.encode()))
    assert result["input_summary"]["raw_count"] == 101
    assert result["metrics"]["detected_count"] == 1
    assert result["metrics"]["false_positive_count"] == 100


@pytest.mark.parametrize("mission", MISSIONS)
def test_empty_csv_for_each_mission(client, mission):
    result = exported(client, post_csv(client, mission, b"channel,starttime,endtime\n"))
    assert result["metrics"]["f0_5"] == 0
    assert result["metrics"]["missed_count"] == result["metrics"]["event_count"]


def test_partial_macro_denominators_and_csv_null_are_explicit(client):
    response = client.post("/", data=dict(fields("Mission1", "manual"), range_start="2024-01-01T00:00:25Z",
        range_end="2024-01-01T00:00:35Z", confirm_range="yes"))
    result = exported(client, response)
    assert result["aggregation"]["valid_channel_counts"]["f0_5"] == 1
    assert result["metrics"]["f0_5"] == 0
    assert result["channel_results"][1]["metrics"]["f0_5"] is None
    text = response.get_data(as_text=True)
    assert "1 / 2 个通道参与" in text and "未参与 F0.5 宏平均" in text
    rows = result_csv_table(client, response)
    assert rows[2]["通道"] == "channel_2" and rows[2]["F0.5"] == "不适用"
    assert "没有可评测真实事件" in rows[2]["备注"]


def test_invalid_blocks_entire_score_and_errors_download(client):
    content = b"channel,starttime,endtime\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\nBAD,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n"
    response = post_csv(client, content=content)
    assert 'id="evaluation-result"' not in response.get_data(as_text=True)
    errors = client.post("/download/errors", data={"token": extract_token(response, "errors")})
    assert "3,通道" in errors.get_data(as_text=True)


def test_no_manual_csv_mixing_and_empty_manual(client):
    response = post_csv(client, channel="BAD", starttime="broken", endtime="broken")
    assert exported(client, response)["metrics"] == exported(client, post_csv(client))["metrics"]
    response = client.post("/", data=dict(fields(mode="manual"), prediction_csv=(io.BytesIO(b"bad"), "bad.csv")))
    assert exported(client, response)["metrics"]["f0_5"] == 0


def test_unknown_channel_preserved_manual(client):
    response = client.post("/", data=dict(fields("Mission2", "manual"), channel="channel_2",
        starttime="2024-02-01T00:00:12Z", endtime="2024-02-01T00:00:18Z"))
    text = response.get_data(as_text=True)
    assert 'value="channel_2"' in text
    assert 'value="2024-02-01T00:00:12Z"' in text
    assert "通道不属于当前 Mission" in text


def test_invalid_version_mission_source(client):
    for change in (dict(mission="Mission4"), dict(source="unknown"), dict(version="old")):
        data = fields(mode="manual")
        data.update(change)
        response = client.post("/", data=data)
        assert 'id="evaluation-result"' not in response.get_data(as_text=True)
        assert 'role="alert"' in response.get_data(as_text=True)


def test_real_unconfigured_does_not_fallback(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"dataset_root: '{tmp_path.as_posix()}'\n", encoding="utf-8")
    client = create_app(dict(TESTING=True, SETTINGS_FILE=settings, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    text = client.get("/?mission=Mission3&source=real").get_data(as_text=True)
    assert "真实数据未配置" in text
    assert 'id="evaluation-result"' not in text


def test_yaml_error_displayed_and_demo_remains_available(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("dataset_root: null\n", encoding="utf-8")
    client = create_app(dict(TESTING=True, SETTINGS_FILE=settings, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    text = client.get("/?mission=Mission1&source=real").get_data(as_text=True)
    assert "dataset_root 必须为非空路径字符串" in text
    demo = client.get("/?mission=Mission1&source=demo")
    assert demo.status_code == 200
    assert "目标通道（2 个）" in demo.get_data(as_text=True)


@pytest.mark.parametrize("mission", MISSIONS)
def test_yaml_root_drives_real_upload_and_root_change_invalidates_preview(tmp_path, mission):
    # Two separately configured copies prove real-label reads use the YAML root
    # and a preview cannot be reused after changing that field.
    for name in ("first", "second"):
        folder = tmp_path / name / f"ESA-{mission}"
        folder.mkdir(parents=True)
        (folder / "channels.csv").write_text("Channel,Target\nconfigured_channel,YES\n", encoding="utf-8")
        (folder / "labels.csv").write_text("ID,Channel,StartTime,EndTime\nfrom_yaml,configured_channel,2024-01-01T00:00:10Z,2024-01-01T00:00:20Z\n", encoding="utf-8")
        (folder / "anomaly_types.csv").write_text("ID,Category\nfrom_yaml,Anomaly\n", encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"dataset_root: '{(tmp_path / 'first').as_posix()}'\n", encoding="utf-8")
    client = create_app(dict(TESTING=True, SETTINGS_FILE=settings, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    data = dict(fields(mission), source="real", version=load_config(mission,"real")["version"],
                range_start="2024-01-01T00:00:00Z", range_end="2024-01-01T00:01:40Z", confirm_range="yes")
    content = b"channel,starttime,endtime\nconfigured_channel,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n"
    scored = client.post("/", data=dict(data, prediction_csv=(io.BytesIO(content), "predictions.csv")))
    result = exported(client, scored)
    assert result["metrics"]["f0_5"] == 1
    assert result["events"][0]["id"] == "from_yaml"
    preview = client.post("/", data=dict(data, action="preview", prediction_csv=(io.BytesIO(content), "predictions.csv")))
    token = re.search(r'id="upload-token"[^>]*value="([^"]+)"', preview.get_data(as_text=True))[1]
    settings.write_text(f"dataset_root: '{(tmp_path / 'second').as_posix()}'\n", encoding="utf-8")
    response = client.post("/", data=dict(data, upload_token=html.unescape(token)))
    assert "旧预览失效" in response.get_data(as_text=True)
    assert 'id="evaluation-result"' not in response.get_data(as_text=True)


def test_upload_and_record_limits_web(tmp_path):
    client = create_app(dict(TESTING=True, MAX_CSV_BYTES=100, MAX_RECORDS=1, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    response = post_csv(client, content=b"x" * 101)
    assert "大小上限" in response.get_data(as_text=True)
    client = create_app(dict(TESTING=True, MAX_CSV_BYTES=1000, MAX_RECORDS=1, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    response = post_csv(client)
    assert "记录数量超过上限" in response.get_data(as_text=True)
    client = create_app(dict(TESTING=True, MAX_CONTENT_LENGTH=100, LEADERBOARD_DIR=tmp_path / "submissions")).test_client()
    assert post_csv(client).status_code == 413


def test_forged_export_blocked(client):
    assert client.post("/download/csv", data=dict(token="forged")).status_code == 400


def test_large_preview_form_field_uses_configured_request_limit(client):
    # A preview token can exceed Flask's default 500-kB per-field limit.
    # This deliberately invalid token must reach signature validation, not 413.
    response = client.post("/", data=dict(fields(), upload_token="x" * 600000))
    assert response.status_code == 200
    assert "凭据已失效" in response.get_data(as_text=True)


def test_real_requires_independent_range():
    cfg = load_config("Mission1", "real")
    cfg.update(start=None, end=None)
    with pytest.raises(DataError, match="缺少固定评测时间范围"):
        load_dataset(cfg)


@pytest.mark.parametrize("mission", MISSIONS)
def test_scanned_real_defaults_are_prefilled_even_with_label_quality_errors(client, mission):
    cfg = load_config(mission, "real")
    if not all(__import__("pathlib").Path(p).exists() for p in cfg["paths"].values()):
        pytest.skip("真实 ESA 标签未配置")
    page = client.get("/", query_string=dict(mission=mission, source="real")).get_data(as_text=True)
    assert f'name="range_start" value="{cfg["start"]}"' in page
    assert f'name="range_end" value="{cfg["end"]}"' in page
    assert "目标通道（" in page
    scored = client.post("/", data=dict(fields(mission, "manual"), source="real", version=cfg["version"],
        range_start=cfg["start"], range_end=cfg["end"]))
    if mission == "Mission3":
        assert "当前范围不可评分" in page
        assert "开始晚于结束" in scored.get_data(as_text=True)
        assert 'id="evaluation-result"' not in scored.get_data(as_text=True)
        metadata = load_dataset(cfg, require_range=False)
        from evaluator import evaluate
        with pytest.raises(DataError, match="无效真实标签"):
            evaluate(metadata, [])
    else:
        result = exported(client, scored)
        assert result["metrics"]["f0_5"] == 0
        assert not result["config"].get("custom_range", False)


@pytest.mark.parametrize("mission", MISSIONS)
def test_configured_range_prefilled_and_does_not_require_custom_confirmation(client, mission):
    cfg = load_config(mission, "demo")
    page = client.get("/", query_string=dict(mission=mission, source="demo")).get_data(as_text=True)
    assert f'name="range_start" value="{cfg["start"]}"' in page
    assert f'name="range_end" value="{cfg["end"]}"' in page
    response = client.post("/", data=dict(fields(mission, "manual"), range_start=cfg["start"], range_end=cfg["end"]))
    result = exported(client, response)
    assert result["metrics"]["f0_5"] == 0
    assert not result["config"].get("custom_range", False)


def test_changed_default_range_still_requires_confirmation(client):
    response = client.post("/", data=dict(fields(mode="manual"),
        range_start="2024-01-01T00:00:00Z", range_end="2024-01-01T00:01:30Z"))
    text = response.get_data(as_text=True)
    assert "请明确勾选确认自定义评测窗口" in text
    assert 'name="range_end" value="2024-01-01T00:01:30Z"' in text
    assert 'id="evaluation-result"' not in text


def test_configured_range_comparison_uses_utc_instants(client):
    response = client.post("/", data=dict(fields(mode="manual"),
        range_start="2024-01-01T08:00:00+08:00", range_end="2024-01-01T08:01:40+08:00"))
    result = exported(client, response)
    assert not result["config"].get("custom_range", False)


@pytest.mark.parametrize("mission", MISSIONS)
def test_real_data_pipeline_smoke(client, mission):
    """Explicit custom-window pipeline self-check, NOT model performance."""
    cfg = load_config(mission, "real")
    if not all(__import__("pathlib").Path(p).exists() for p in cfg["paths"].values()):
        pytest.skip("真实 ESA 标签未配置")
    # Mission3 has reversed source-label rows in late 2004. Use an explicit,
    # disjoint fixed window and retain data-quality warnings in the result.
    end = "2004-01-01T00:00:00Z" if mission == "Mission3" else "2025-01-01T00:00:00Z"
    dataset = load_dataset(cfg, start="2000-01-01T00:00:00Z", end=end, confirmed=True)
    row = next(r for r in dataset["labels"] if r["category"] == "Anomaly")
    content = f"channel,starttime,endtime\n{row['channel']},{iso(row['start'])},{iso(row['end'])}\n".encode()
    data = dict(mission=mission, source="real", version=cfg["version"], category="both", mode="csv", action="evaluate",
                range_start="2000-01-01T00:00:00Z", range_end=end, confirm_range="yes",
                prediction_csv=(io.BytesIO(content), "pipeline-self-check.csv"))
    response = client.post("/", data=data)
    result = exported(client, response)
    assert result["config"]["mission_id"] == mission
    assert result["config"]["source"] == "real"
    assert result["metrics"]["detected_count"] >= 1
    assert result["metrics"]["nominal_seconds"] > 0
    assert result["config"]["custom_range"] is True
    if mission == "Mission3":
        assert result["config"]["label_warnings"]


def test_mission3_bad_real_labels_are_not_repaired_or_silently_dropped():
    cfg = load_config("Mission3", "real")
    if not __import__("pathlib").Path(cfg["paths"]["labels"]).exists():
        pytest.skip("真实 ESA 标签未配置")
    with pytest.raises(DataError, match="开始晚于结束"):
        load_dataset(cfg, start="2000-01-01T00:00:00Z", end="2025-01-01T00:00:00Z", confirmed=True)
