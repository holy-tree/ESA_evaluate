import html
import json
import re

import pytest

from datasets import MISSIONS
from test_app import exported, fields, post_csv


def test_source_selector_hidden_default_real_and_custom_entry_reserved(client):
    page=client.get("/").get_data(as_text=True)
    assert '<input type="hidden" id="source" name="source" value="real">' in page
    assert '<select id="source"' not in page
    assert 'value="custom" disabled' in page and "自定义数据集（预留）" in page
    for method in (client.get,client.post):
        response=method("/api/datasets/custom")
        assert response.status_code == 501
        assert response.get_json()["enabled"] is False
        assert "metrics" not in response.get_json()


def test_explicit_demo_stays_visibly_marked(client):
    page=client.get("/?mission=Mission1&source=demo").get_data(as_text=True)
    assert '<input type="hidden" id="source" name="source" value="demo">' in page
    assert '<span class="badge demo">演示数据</span>' in page


def test_requested_verbose_copy_removed_but_preview_still_scores(client):
    preview=post_csv(client,action="preview")
    text=preview.get_data(as_text=True)
    removed=["真实 ESA 标签；默认窗口来自", "未声明官方训练/测试划分", "使用自定义窗口时，我确认以上起止时间",
        "默认填入当前 Mission 配置的观测范围", "评分协议：每个目标通道只使用自身标签与预测",
        "用户 ID（上传可留空，手动必填）", "上传 CSV／JSON 未填写 ID 时", "本文件默认 ID：",
        "的完整上传数据，可直接开始评测", "10 条预测样例在 Anomaly + Rare Event 下为",
        "演示文件只能用于对应", "字段名忽略大小写及首尾空格", "时间按 ISO-8601 输入",
        "各通道独立匹配；总体直接平均通道分数", "单次评测仅使用一种输入模式"]
    assert all(sentence not in text for sentence in removed)
    assert 'id="identity-hint"' not in text and 'id="identity-help"' not in text
    token=html.unescape(re.search(r'id="upload-token"[^>]*value="([^"]+)"',text)[1])
    result=exported(client,client.post("/",data=dict(fields(),upload_token=token)))
    assert result["input_summary"]["raw_count"] == 10


@pytest.mark.parametrize("mission",MISSIONS)
def test_channel_details_are_nested_with_their_own_summary_and_raw_sort_values(client,mission):
    response=post_csv(client,mission)
    result=exported(client,response)
    text=response.get_data(as_text=True)
    assert "逐通道真实事件检出 / 漏检</h3>" not in text
    assert ">真实事件</th>" not in text and ">状态／原因</th>" not in text
    groups=re.findall(r'<tbody class="channel-group" data-channel="([^"]+)" data-original-index="(\d+)" data-metrics="([^"]+)">(.*?)(?=<tbody class="channel-group"|<tfoot id="channel-table-empty")',text,re.S)
    assert len(groups) == len(result["channel_results"])
    for (channel,index,metrics,body), row in zip(groups,result["channel_results"]):
        assert channel == row["channel"]
        assert json.loads(html.unescape(metrics)) == row["metrics"]
        assert f'id="channel-detail-{index}" class="channel-details" hidden' in body
        assert 'aria-expanded="false"' in body
        assert f'data-chart-channel="{channel}"' in body
        assert '查看该通道曲线' in body
        actual_ids=re.findall(r'data-event-id="([^"]+)"',body)
        assert actual_ids == [e["id"] for e in row["events"]]
    header=text.split('<table id="channel-scores">',1)[1].split('</thead>',1)[0]
    assert header.count('class="channel-sort"') == 9
    assert header.count('class="channel-filter-button"') == 9
    assert 'id="channel-search"' in text and 'id="channel-filter-panel"' in text


def test_hidden_columns_remain_available_in_complete_export(client):
    result=exported(client,post_csv(client))
    assert all("event_count" in c["metrics"] and "status" in c and "reasons" in c for c in result["channel_results"])
