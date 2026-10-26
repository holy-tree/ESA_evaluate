"""Read real channel windows through the chart API for all three Missions."""
import html
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from datasets import MISSIONS, iso, load_config, load_dataset


def main():
    client = create_app({"TESTING": True}).test_client()
    results = []
    for mission in MISSIONS:
        cfg = load_config(mission, "real")
        page = client.get("/", query_string=dict(mission=mission, source="real"))
        match = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', page.get_data(as_text=True), re.S)
        data = json.loads(match[1])
        for scale in ("day", "month", "year"):
            response = client.post("/api/series", json=dict(kind="chart", token=data["chart"]["token"],
                channel=data["chart"]["channel"], date=cfg["start"][:10], scale=scale))
            chart = response.get_json()
            assert response.status_code == 200, chart
            assert chart["raw_count"] > 0 and chart["segments"]
            assert chart["displayed_count"] <= 8000
            results.append(dict(mission=mission, channel=chart["channel"], scale=scale, kind=chart["kind"],
                raw_points=chart["raw_count"], displayed_points=chart["displayed_count"], status="PASS"))
        # Labels-derived predictions verify the plotting pipeline, not model performance.
        if mission == "Mission3":
            ds = load_dataset(cfg, start="2000-01-01T00:00:00Z", end="2004-01-01T00:00:00Z", confirmed=True)
        else:
            ds = load_dataset(cfg)
        label = next(row for row in ds["labels"] if row["category"] == "Anomaly")
        fields = dict(mission=mission, source="real", version=cfg["version"], category="both", mode="manual", action="evaluate",
            channel=label["channel"], starttime=iso(label["start"]), endtime=iso(label["end"]))
        if mission == "Mission3":
            fields.update(range_start=ds["config"]["start"], range_end=ds["config"]["end"], confirm_range="yes")
        scored = client.post("/", data=fields)
        token = re.search(r'action="/download/json"[^>]*>\s*<input[^>]*value="([^"]+)"', scored.get_data(as_text=True))[1]
        response = client.post("/api/series", json=dict(kind="result", token=html.unescape(token), channel=label["channel"],
            date=iso(label["start"])[:10], scale="day"))
        chart = response.get_json()
        assert response.status_code == 200 and chart["detections"], chart
        results.append(dict(mission=mission, channel=chart["channel"], scale="day-with-detection", kind=chart["kind"],
            purpose="labels-derived pipeline self-check, not model performance", status="PASS"))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
