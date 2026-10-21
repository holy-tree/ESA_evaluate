import pytest

from app import create_app
from datasets import load_config, load_dataset, timestamp


@pytest.fixture
def client():
    return create_app({"TESTING": True, "SECRET_KEY": "test-key"}).test_client()


@pytest.fixture
def demo():
    return lambda mission="Mission1": load_dataset(load_config(mission, "demo"))


def synthetic(labels=None):
    from copy import deepcopy
    dataset = load_dataset(load_config("Mission1", "demo"))
    dataset = deepcopy(dataset)
    dataset["labels"] = []
    for event_id, a, b, category in labels or [("A", 10, 20, "Anomaly"), ("B", 40, 50, "Anomaly")]:
        dataset["labels"].append(dict(id=event_id, channel="channel_1", start=at(a), end=at(b), category=category))
    return dataset


def at(second):
    import pandas as pd
    return timestamp("2024-01-01T00:00:00Z") + pd.Timedelta(seconds=second)


def predictions(*intervals):
    return [dict(channel="channel_2", start=at(a), end=at(b)) for a, b in intervals]
