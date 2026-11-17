import io
import json
from pathlib import Path

import pytest

from datasets import BASE, MISSIONS, load_config, load_dataset, read_csv
from scripts.check_prediction_samples import verify_sample
from test_app import exported, fields


@pytest.mark.parametrize("mission",MISSIONS)
def test_enriched_demo_samples_have_eight_hits_two_false_alarms(mission):
    ds=load_dataset(load_config(mission,"demo"))
    result=verify_sample(ds,BASE/"demo_data"/mission/"predictions.csv")
    assert result["records"] == 10 and result["channels"] == 2
    assert result["matched_predictions"] == 8 and result["false_positive_intervals"] == 2


@pytest.mark.parametrize("mission",MISSIONS)
def test_real_samples_match_supplied_labels_and_web_pipeline(client,mission):
    manifest=json.loads((BASE/"sample_predictions"/"manifest.json").read_text(encoding="utf-8"))
    entry=manifest["missions"][mission]
    cfg=load_config(mission,"real",entry["version"])
    if not all(Path(p).exists() for p in cfg["paths"].values()):
        pytest.skip("真实 ESA 标签未配置")
    kwargs={} if (cfg["start"],cfg["end"]) == (entry["start"],entry["end"]) else dict(start=entry["start"],end=entry["end"],confirmed=True)
    ds=load_dataset(cfg,**kwargs)
    path=BASE/"sample_predictions"/entry["file"]
    checked=verify_sample(ds,path,entry["expected_event_ids_by_record"])
    _,rows=read_csv(path)
    assert sorted({r["channel"] for r in rows}) == entry["channels"]
    response=client.post("/",data=dict(fields(mission),source="real",version=entry["version"],user_id="",
        range_start=entry["start"],range_end=entry["end"],confirm_range="yes",
        prediction_csv=(io.BytesIO(path.read_bytes()),path.name)))
    result=exported(client,response)
    assert result["input_summary"]["raw_count"] == 10
    assert result["metrics"]["false_positive_count"] == 2
    assert sum(r["matched_selected"] for r in result["predictions"]) == 8
    assert result["metrics"]["f0_5"] == checked["macro_f0_5"]
    assert result["submission"]["user_id"] == path.name
    assert result["config"]["selected_categories"] == ["Anomaly", "Rare Event", "Communication Gap"]
    if mission == "Mission1":
        assert result["config"]["start"] == "2007-01-01T00:00:00Z"
        assert result["navigation_range"][0] == "2000-01-01T00:00:00.969000Z"
        assert len(result["channel_results"]) == 58
        assert result["aggregation"]["valid_channel_counts"]["f0_5"] == 52
        assert not any(e["category"] == "Communication Gap" for e in result["events"])


@pytest.mark.parametrize("mission",MISSIONS)
def test_real_sample_downloads_are_separate_from_demo(client,mission):
    csv_response=client.get(f"/samples/{mission}/predictions.csv")
    json_response=client.get(f"/samples/{mission}/predictions.json")
    assert csv_response.status_code == json_response.status_code == 200
    assert "PIPELINE-CHECK-" in csv_response.headers["Content-Disposition"]
    _,rows=read_csv(BASE/"sample_predictions"/f"PIPELINE-CHECK-{mission}.csv")
    assert len(rows) == 10 and json_response.get_json() == rows
    page=client.get(f"/?mission={mission}&source=real").get_data(as_text=True)
    assert f'/samples/{mission}/predictions.csv' in page
    assert f'/demo/{mission}/predictions.csv' not in page
    if mission == "Mission3":
        assert "2004-01-01T00:00:00Z" in page
    demo=client.get(f"/?mission={mission}&source=demo").get_data(as_text=True)
    assert f'/demo/{mission}/predictions.csv' in demo
    assert f'/samples/{mission}/predictions.csv' not in demo


def test_sample_guide_and_invalid_downloads(client):
    guide=client.get("/samples/guide")
    assert guide.status_code == 200 and "不是算法性能结果" in guide.get_data(as_text=True)
    assert client.get("/samples/Mission4/predictions.csv").status_code == 404
    assert client.get("/samples/Mission1/predictions.zip").status_code == 404
