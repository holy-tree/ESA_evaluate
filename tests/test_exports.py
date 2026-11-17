import csv
import io

import pytest

from test_app import exported, extract_token, post_csv, result_csv_table


def test_single_readable_csv_contains_metadata_overall_and_all_channels(client):
    response = post_csv(client, user_id="实验模型 A")
    text = response.get_data(as_text=True)
    assert text.count('action="/download/csv"') == 1
    for old in ("json", "channels", "events"):
        assert f'action="/download/{old}"' not in text
    result = exported(client, response)
    download = client.post("/download/csv", data=dict(token=extract_token(response, "csv")))
    assert download.mimetype == "text/csv"
    assert download.data.startswith(b"\xef\xbb\xbf")
    assert "-results.csv" in download.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(download.get_data(as_text=True).lstrip("\ufeff"))))
    assert all(len(row) == 12 for row in rows)
    assert rows[0][0] == "ESA 评测结果"
    assert any(row[:2] == ["用户 ID", "实验模型 A"] for row in rows)
    assert any(result["submission"]["submitted_at"] in row for row in rows)
    assert "label_fragments" not in download.get_data(as_text=True)
    table = result_csv_table(client, response)
    assert len(table) == len(result["channel_results"]) + 1
    assert table[0]["范围"] == "总体"
    assert float(table[0]["F0.5"]) == pytest.approx(result["metrics"]["f0_5"], abs=5e-7)
    assert int(table[0]["事件数"]) == result["metrics"]["event_count"]
    assert [row["通道"] for row in table[1:]] == result["config"]["target_channels"]


def test_csv_user_text_is_escaped_and_numeric_zero_remains_numeric(client):
    response = post_csv(client, content=b"channel,starttime,endtime\n", user_id="=SUM(1,2)")
    download = client.post("/download/csv", data=dict(token=extract_token(response, "csv")))
    rows = list(csv.reader(io.StringIO(download.get_data(as_text=True).lstrip("\ufeff"))))
    assert next(row for row in rows if row[0] == "用户 ID")[1] == "'=SUM(1,2)"
    assert result_csv_table(client, response)[0]["F0.5"] == "0.000000"


@pytest.mark.parametrize("old_format", ["json", "channels", "events"])
def test_previous_result_download_formats_are_replaced_by_csv(client, old_format):
    response = post_csv(client)
    download = client.post(f"/download/{old_format}", data=dict(token=extract_token(response, "csv")))
    assert download.status_code == 400
    assert "统一使用 CSV" in download.get_data(as_text=True)
