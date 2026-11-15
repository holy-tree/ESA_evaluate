from concurrent.futures import ThreadPoolExecutor
import io
import json

import pytest

from app import create_app
from leaderboard import IdentityError, LeaderboardError, LeaderboardStore, ranking_scope, resolve_identity
from test_app import exported, fields, post_csv
from test_json_input import encode, record, upload_token


def fake_result(score=.5):
    return dict(config=dict(mission_id="Mission1", version="fixture-1", source="demo", category_mode="both",
        start="2024-01-01T00:00:00Z", end="2024-01-01T00:01:40Z", target_channels=["a","b"]),
        metrics=dict(f0_5=score), data_fingerprint="labels-fixture-1")


def scope_of(result):
    cfg=result["config"]
    return ranking_scope(cfg,cfg["target_channels"],cfg["category_mode"],result["data_fingerprint"])


def save(store, user_id, score, second=0):
    return store.record(fake_result(score),user_id,"provided","a.csv","csv",f"2024-01-01T00:00:{second:02d}Z")


def test_best_per_id_sorting_timestamp_and_restart(tmp_path):
    store=LeaderboardStore(tmp_path)
    first=save(store,"alice",.8,1)
    lower=save(store,"alice",.4,2)
    save(store,"bob",.9,3)
    board=LeaderboardStore(tmp_path).board()
    assert [e["user_id"] for e in board["entries"]] == ["bob","alice"]
    assert board["entries"][1]["submitted_at"] == first["submitted_at"]
    assert not lower["best_updated"] and lower["best_score"] == .8
    higher=save(store,"alice",.95,4)
    tied=save(store,"alice",.95,5)
    board=store.board()
    assert len(board["entries"]) == 2
    assert board["entries"][0]["submission_id"] == higher["submission_id"]
    assert not tied["best_updated"]
    assert board["total_submissions"] == 5
    assert all(e["submitted_at"].endswith("Z") for e in board["entries"])


def test_equal_score_uses_earliest_submission_then_id(tmp_path):
    store=LeaderboardStore(tmp_path)
    save(store,"later",.5,2)
    save(store,"z",.5,1)
    save(store,"a",.5,1)
    entries=store.board()["entries"]
    assert [e["user_id"] for e in entries] == ["a","z","later"]


@pytest.mark.parametrize("field,value", [("mission_id","Mission2"),("version","fixture-2"),("source","real"),
    ("category_mode","anomaly"),("start","2024-01-01T00:00:01Z"),("end","2024-01-01T00:01:30Z"),
    ("target_channels",["a"]),("evaluation_protocol","future-protocol"),("beta",1.0)])
def test_unified_rankings_keep_best_across_configurations(tmp_path,field,value):
    store=LeaderboardStore(tmp_path)
    save(store,"same-user",.6)
    changed=fake_result(.9)
    changed["config"][field]=value
    store.record(changed,"same-user","provided","a.json","json")
    assert scope_of(changed)["key"] != scope_of(fake_result())["key"]
    board=store.board()
    assert board["total_submissions"] == 2
    assert len(board["entries"]) == 1
    assert board["entries"][0]["score"] == .9
    assert board["entries"][0]["scope"][field] == value


def test_labels_fingerprint_is_preserved_and_channel_order_is_canonical(tmp_path):
    store=LeaderboardStore(tmp_path)
    save(store,"user",.6)
    changed=fake_result(.8)
    changed["data_fingerprint"]="new-labels"
    store.record(changed,"user","provided",None,"manual")
    assert store.board()["entries"][0]["score"] == .8
    assert store.board()["entries"][0]["scope"]["data_fingerprint"] == "new-labels"
    ordered=fake_result()
    ordered["config"]["target_channels"].reverse()
    assert scope_of(ordered)["key"] == scope_of(fake_result())["key"]


def test_null_scores_are_saved_but_not_ranked_and_zero_is_ranked(tmp_path):
    store=LeaderboardStore(tmp_path)
    unranked=save(store,"user",None)
    assert not unranked["eligible"]
    board=store.board()
    assert board["total_submissions"] == 1 and not board["entries"]
    zero=save(store,"user",0,1)
    assert zero["eligible"] and zero["best_updated"]
    assert store.board()["entries"][0]["score"] == 0


def test_concurrent_instances_preserve_all_submissions(tmp_path):
    def submit(i):
        return save(LeaderboardStore(tmp_path),f"user-{i}",i/50,i)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(submit,range(20)))
    board=LeaderboardStore(tmp_path).board()
    assert board["total_submissions"] == len(board["entries"]) == 20
    assert board["entries"][0]["user_id"] == "user-19"
    assert not list(tmp_path.glob(".leaderboard-*.tmp"))


def test_corrupt_file_is_never_overwritten(tmp_path):
    path=tmp_path/"leaderboard.json"
    path.write_text("{broken",encoding="utf-8")
    store=LeaderboardStore(tmp_path)
    with pytest.raises(LeaderboardError,match="不会覆盖"):
        save(store,"user",.5)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_identity_rules():
    assert resolve_identity("  User-1  ","manual") == ("User-1","provided")
    assert resolve_identity("", "json",r"C:\fakepath\实验A.json") == ("实验A.json","filename")
    assert resolve_identity("", "csv","/some/path/test.csv") == ("test.csv","filename")
    with pytest.raises(IdentityError,match="手动提交必须"):
        resolve_identity("  ","manual")
    for value in ("x"*129,"a\nb"):
        with pytest.raises(IdentityError):
            resolve_identity(value,"csv","a.csv")


@pytest.mark.parametrize("mode",["csv","json"])
def test_file_name_fallback_keeps_unicode_basename(client,mode):
    filename=f"实验-A.{mode}"
    content=b"channel,starttime,endtime\nchannel_1,2024-01-01T00:00:12Z,2024-01-01T00:00:18Z\n" if mode=="csv" else encode([record()])
    response=client.post("/",data=dict(fields(mode=mode),user_id="",**{f"prediction_{mode}":(io.BytesIO(content),"/some/path/"+filename)}))
    result=exported(client,response)
    assert result["submission"]["user_id"] == result["submission"]["filename"] == filename
    assert result["submission"]["id_source"] == "filename"
    assert result["submission"]["persisted"]
    assert filename in client.get("/leaderboard").get_data(as_text=True)


def test_explicit_identity_overrides_filename_and_manual_missing_id_is_rejected(client):
    result=exported(client,post_csv(client,user_id="  student-1  "))
    assert result["submission"]["user_id"] == "student-1"
    response=client.post("/",data=dict(fields(mode="manual"),user_id="",channel="channel_1",starttime="2024-01-01T00:00:12Z",endtime="2024-01-01T00:00:18Z"))
    text=response.get_data(as_text=True)
    assert "手动提交必须填写用户 ID" in text
    assert 'id="evaluation-result"' not in text
    assert 'value="2024-01-01T00:00:12Z"' in text


def test_preview_and_readonly_board_do_not_record_submissions(client):
    preview=post_csv(client,action="preview",user_id="")
    path=client.application.config["LEADERBOARD_DIR"] / "leaderboard.json"
    assert not path.exists()
    response=client.post("/",data=dict(fields(mode="manual"),user_id="",action="leaderboard"))
    assert response.status_code == 200 and not path.exists()
    assert "手动提交必须填写用户 ID" not in response.get_data(as_text=True)
    final=client.post("/",data=dict(fields(),user_id="",upload_token=upload_token(preview)))
    assert exported(client,final)["submission"]["user_id"] == "prediction.csv"
    assert len(json.loads(path.read_text(encoding="utf-8"))["submissions"]) == 1


def test_identity_error_keeps_file_preview_for_correction(client):
    response=post_csv(client,user_id="x"*129)
    text=response.get_data(as_text=True)
    assert "用户 ID 最多 128" in text and 'id="evaluation-result"' not in text
    corrected=client.post("/",data=dict(fields(),user_id="short-id",upload_token=upload_token(response)))
    assert exported(client,corrected)["submission"]["user_id"] == "short-id"


def test_invalid_predictions_do_not_create_ranked_history(client):
    response=post_csv(client,content=b"wrong,columns\n",user_id="user")
    assert 'id="evaluation-result"' not in response.get_data(as_text=True)
    assert not (client.application.config["LEADERBOARD_DIR"] / "leaderboard.json").exists()


def test_default_input_priority_and_result_precedes_visualization(client):
    page=client.get("/?mission=Mission1&source=demo").get_data(as_text=True)
    assert 'name="mode" value="csv" checked' in page
    assert page.index('name="mode" value="csv"') < page.index('name="mode" value="json"') < page.index('name="mode" value="manual"')
    response=post_csv(client)
    text=response.get_data(as_text=True)
    assert text.index('id="evaluation-result"') < text.index('id="raw-visualization"') < text.index('id="leaderboard"')


def test_same_id_only_best_submission_is_shown_after_restart(tmp_path):
    directory=tmp_path/"rankings"
    first=create_app(dict(TESTING=True,SECRET_KEY="first-process",LEADERBOARD_DIR=directory)).test_client()
    original=exported(first,post_csv(first,"Mission2",user_id="alice"))
    lower=exported(first,post_csv(first,"Mission2",b"channel,starttime,endtime\n",user_id="alice"))
    assert original["metrics"]["f0_5"] > 0 and lower["metrics"]["f0_5"] == 0
    assert not lower["submission"]["best_updated"]
    second=create_app(dict(TESTING=True,SECRET_KEY="another-process",LEADERBOARD_DIR=directory)).test_client()
    page=second.get("/leaderboard",query_string=dict(scope=original["submission"]["scope_key"]))
    text=page.get_data(as_text=True)
    assert text.count('data-user-id="alice"') == 1
    assert original["submission"]["submitted_at"] in text
    assert lower["submission"]["submitted_at"] not in text
    unified=second.get("/leaderboard?scope=unknown")
    assert unified.status_code == 200
    assert 'data-user-id="alice"' in unified.get_data(as_text=True)
    assert 'id="board-scope"' not in text


def test_persistence_failure_keeps_score_available_and_file_intact(client):
    directory=client.application.config["LEADERBOARD_DIR"]
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/"leaderboard.json"
    path.write_text("broken",encoding="utf-8")
    response=post_csv(client)
    result=exported(client,response)
    assert result["metrics"]["f0_5"] is not None
    assert not result["submission"]["persisted"]
    assert "排行榜保存失败" in response.get_data(as_text=True)
    assert path.read_text(encoding="utf-8") == "broken"
    assert client.get("/leaderboard").status_code == 503
