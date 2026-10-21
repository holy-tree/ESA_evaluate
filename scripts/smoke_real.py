"""Explicit custom-range, real-label pipeline self-check. NOT model performance."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from datasets import MISSIONS, DataError, iso, load_config, load_dataset


def main():
    client = create_app({"TESTING": True}).test_client()
    summaries = []
    for mission in MISSIONS:
        try:
            cfg = load_config(mission, "real")
            end = "2004-01-01T00:00:00Z" if mission == "Mission3" else "2025-01-01T00:00:00Z"
            ds = load_dataset(cfg, start="2000-01-01T00:00:00Z", end=end, confirmed=True)
            row = next(r for r in ds["labels"] if r["category"] == "Anomaly")
            csv = f"channel,starttime,endtime\n{row['channel']},{iso(row['start'])},{iso(row['end'])}\n".encode()
            response = client.post("/", data=dict(mission=mission, source="real", version=cfg["version"], category="both",
                mode="csv", action="evaluate", range_start="2000-01-01T00:00:00Z", range_end=end,
                confirm_range="yes", prediction_csv=(io.BytesIO(csv), "PIPELINE-SELF-CHECK.csv")))
            assert response.status_code == 200 and 'id="evaluation-result"' in response.get_data(as_text=True)
            summaries.append(dict(mission=mission, status="PASS", target_channels=len(ds["channels"]),
                label_fragments_in_custom_window=len(ds["labels"]), categories=sorted({r["category"] for r in ds["labels"]}),
                label_warnings=ds["config"]["label_warnings"],
                version=cfg["version"], protocol=f"管线自检；2000-01-01T00:00:00Z 至 {end} 自定义窗口；预测由真实标签构造，不是模型性能"))
        except (DataError, StopIteration) as exc:
            summaries.append(dict(mission=mission, status="NOT_CONFIGURED", reason=str(exc)))
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if any(s["status"] != "PASS" for s in summaries):
        sys.exit(1)


if __name__ == "__main__":
    main()
