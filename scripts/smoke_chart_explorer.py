"""Real-data smoke checks for the continuous, multi-channel display API."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app import create_app
from datasets import MISSIONS, iso, load_config, timestamp


def main():
    client = create_app({"TESTING": True}).test_client()
    results = []
    for mission in MISSIONS:
        cfg = load_config(mission, "real")
        response = client.get("/", query_string=dict(mission=mission, source="real"))
        page = json.loads(re.search(r'<script type="application/json" id="page-data">(.*?)</script>', response.get_data(as_text=True), re.S)[1])
        start = timestamp(cfg["start"])
        stop = min(start+pd.DateOffset(years=1), timestamp(cfg["end"]))
        names = page["channels"][:2]
        for mode in ("auto", "moving", "kernel"):
            request = dict(kind="chart", token=page["chart"]["token"], channels=names,
                view_start=iso(start), view_end=iso(stop), display_mode=mode, pixel_width=1000)
            response = client.post("/api/series", json=request)
            data = response.get_json()
            assert response.status_code == 200, data
            assert len(data["channels"]) == 2 and all(c["raw_count"] > 0 for c in data["channels"])
            assert data["evaluation_range"] == [cfg["start"], cfg["end"]]
            if mission == "Mission3":
                assert all(c["mode"] == "states" and not c["bins"] for c in data["channels"])
            elif mode == "auto":
                assert all(c["mode"] == "aggregate" and len(c["bins"]) < 3000 for c in data["channels"])
            results.append(dict(mission=mission, channels=names, requested_mode=mode,
                effective_modes=[c["mode"] for c in data["channels"]],
                raw_points=sum(c["raw_count"] for c in data["channels"]),
                display_points=sum(c["displayed_count"] for c in data["channels"]), status="PASS"))
        later = start+pd.DateOffset(years=1)
        request.update(view_start=iso(later), view_end=iso(min(later+pd.Timedelta(days=14), timestamp(cfg["end"]))), display_mode="auto")
        response = client.post("/api/series", json=request)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["window_start"] == iso(later)
        results.append(dict(mission=mission, arbitrary_cross_year_window="PASS"))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
