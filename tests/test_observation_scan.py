import pandas as pd
import pytest

from datasets import DataError, timestamp
from scripts.scan_observation_ranges import channel_bounds


def test_channel_bounds_use_index_min_max_not_positional_endpoints(tmp_path):
    path = tmp_path / "channel_1.zip"
    index = pd.DatetimeIndex(["2024-01-01T00:00:20", "2024-01-01T00:00:10.123456789", "2024-01-01T00:00:15"])
    pd.DataFrame({"value": [1, 2, 3]}, index=index).to_pickle(path)
    result = channel_bounds(path)
    assert timestamp(result["start"]) == timestamp("2024-01-01T00:00:10.123456789Z")
    assert timestamp(result["end"]) == timestamp("2024-01-01T00:00:20Z")
    assert result["sample_count"] == 3
    assert not result["sorted"]


def test_channel_bounds_normalize_timezone(tmp_path):
    path = tmp_path / "channel_1.zip"
    index = pd.DatetimeIndex(["2024-01-01T08:00:00+08:00", "2024-01-01T09:00:00+08:00"])
    pd.DataFrame({"value": [1, 2]}, index=index).to_pickle(path)
    result = channel_bounds(path)
    assert timestamp(result["start"]) == timestamp("2024-01-01T00:00:00Z")
    assert timestamp(result["end"]) == timestamp("2024-01-01T01:00:00Z")


@pytest.mark.parametrize("index", [pd.Index([1, 2]), pd.DatetimeIndex([]), pd.DatetimeIndex([pd.NaT])])
def test_channel_bounds_reject_unusable_indexes(tmp_path, index):
    path = tmp_path / "channel_1.zip"
    pd.DataFrame(index=index).to_pickle(path)
    with pytest.raises(DataError, match="DatetimeIndex"):
        channel_bounds(path)
