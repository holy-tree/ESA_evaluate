from pathlib import Path

import pytest

from datasets import BASE, MISSIONS, DataError, load_config, load_dataset, load_dataset_root


def local_config(tmp_path):
    labels = tmp_path / "labels.csv"
    channels = tmp_path / "channels.csv"
    labels.write_text("ID,Channel,StartTime,EndTime,Category\na,c,2024-01-01T00:00:10Z,2024-01-01T00:00:20Z,Anomaly\n", encoding="utf-8")
    channels.write_text("Channel,Target\nc,YES\nnot_target,NO\n", encoding="utf-8")
    cfg = load_config("Mission1", "demo")
    cfg["paths"] = {"labels": str(labels), "channels": str(channels)}
    return cfg


def test_targets_never_default_all(tmp_path):
    cfg = local_config(tmp_path)
    assert load_dataset(cfg)["channels"] == ["c"]
    Path(cfg["paths"]["channels"]).write_text("Channel,Subsystem\nc,s\n", encoding="utf-8")
    with pytest.raises(DataError, match="无法识别 channels"):
        load_dataset(cfg)
    cfg["target_channels"] = ["c"]
    assert load_dataset(cfg)["channels"] == ["c"]


def test_missing_category_and_id_join(tmp_path):
    cfg = local_config(tmp_path)
    Path(cfg["paths"]["labels"]).write_text("ID,Channel,StartTime,EndTime\na,c,2024-01-01T00:00:10Z,2024-01-01T00:00:20Z\n", encoding="utf-8")
    with pytest.raises(DataError, match="缺少 Category"):
        load_dataset(cfg)
    types = tmp_path / "types.csv"
    types.write_text("ID,Category\na,Rare Event\n", encoding="utf-8")
    cfg["paths"]["anomaly_types"] = str(types)
    assert load_dataset(cfg)["labels"][0]["category"] == "Rare Event"
    types.write_text("ID,Category\nb,Anomaly\n", encoding="utf-8")
    with pytest.raises(DataError, match="缺少事件 ID 或 Category"):
        load_dataset(cfg)


def test_custom_range_requires_confirmation(tmp_path):
    cfg = local_config(tmp_path)
    with pytest.raises(DataError, match="确认"):
        load_dataset(cfg, start="2024-01-01", end="2024-01-02")


@pytest.mark.parametrize("mission", MISSIONS)
def test_dataset_root_yaml_absolute(tmp_path, mission):
    settings = tmp_path / "settings.yaml"
    root = tmp_path / "ESA data"
    settings.write_text(f"dataset_root: '{root.as_posix()}'\n", encoding="utf-8")
    cfg = load_config(mission, "real", settings_file=settings)
    assert Path(cfg["paths"]["labels"]) == root / f"ESA-{mission}" / "labels.csv"


def test_dataset_root_yaml_relative_is_not_cwd_relative(monkeypatch, tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("dataset_root: '../ESA'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_dataset_root(settings) == (BASE.parent / "ESA").resolve()


@pytest.mark.parametrize("content", ["", "other: value", "dataset_root: null", "dataset_root: '  '",
                                   "dataset_root: 42", "dataset_root: []", "- dataset_root", "dataset_root: ["])
def test_dataset_yaml_invalid_reports_clear_error(tmp_path, content):
    settings = tmp_path / "settings.yaml"
    settings.write_text(content, encoding="utf-8")
    with pytest.raises(DataError, match="YAML 配置"):
        load_config("Mission1", "real", settings_file=settings)


def test_missing_yaml_does_not_fall_back_but_demo_still_works(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(DataError, match="无法读取 YAML 配置"):
        load_config("Mission1", "real", settings_file=missing)
    cfg = load_config("Mission1", "demo", settings_file=missing)
    assert load_dataset(cfg)["channels"]
