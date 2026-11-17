"""Compact, spreadsheet-readable result report: metadata, overall and channel rows."""

RATE_COLUMNS = (("f0_5", "F0.5"), ("p_event", "P_event"), ("tnr", "TNR"),
                ("p_corrected", "修正精确率"), ("recall", "召回率"))
COUNT_COLUMNS = (("event_count", "事件数"), ("detected_count", "检出"),
                 ("missed_count", "漏检"), ("false_positive_count", "误报区间"))


def result_csv_rows(result):
    cfg = result["config"]
    submission = result.get("submission", {})
    aggregation = result["aggregation"]
    header = ["范围", "通道", *[title for _, title in RATE_COLUMNS],
              *[title for _, title in COUNT_COLUMNS], "备注"]

    def metrics(values):
        rates = ["不适用" if values[key] is None else f"{values[key]:.6f}" for key, _ in RATE_COLUMNS]
        return [*rates, *[values[key] for key, _ in COUNT_COLUMNS]]

    rows = [
        ["ESA 评测结果"],
        ["Mission", cfg["mission_id"], "数据版本", cfg["version"], "数据来源", "真实 ESA" if cfg["source"] == "real" else "演示数据"],
        ["用户 ID", submission.get("user_id", ""), "提交时间 UTC", submission.get("submitted_at", "")],
        ["评分开始 UTC", cfg["start"], "评分结束 UTC", cfg["end"]],
        ["评分事件类别", " + ".join(cfg["selected_categories"]), "评分协议", cfg["evaluation_protocol"]],
        ["指标有效通道数", "；".join(f"{title}: {aggregation['valid_channel_counts'][key]}/{aggregation['total_channels']}"
                                 for key, title in RATE_COLUMNS)],
        [],
        header,
        ["总体", "通道宏平均", *metrics(result["metrics"]), "比例指标为通道宏平均；事件及误报数量为通道合计"],
    ]
    rows.extend(["通道", row["channel"], *metrics(row["metrics"]), "；".join(row["reasons"])]
                for row in result["channel_results"])
    # Rectangular rows keep the report easy to open in Excel or a CSV reader.
    return [row + [""] * (len(header) - len(row)) for row in rows]
