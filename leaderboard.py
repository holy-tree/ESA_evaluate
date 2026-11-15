"""Local JSON leaderboard, protected by thread/process locks and atomic writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4

from datasets import CATEGORIES
from evaluator import PROTOCOL

_FILE_LOCK = RLock()


class IdentityError(ValueError):
    pass


class LeaderboardError(ValueError):
    pass


def upload_basename(filename):
    return str(filename).replace("\\", "/").rsplit("/", 1)[-1].strip()


def resolve_identity(user_id, mode, filename=None):
    explicit = str(user_id or "").strip()
    value = explicit or (upload_basename(filename) if mode in ("csv", "json") and filename else "")
    if not value:
        raise IdentityError("手动提交必须填写用户 ID；上传 CSV／JSON 时可留空使用文件名。")
    if len(value) > 128:
        raise IdentityError("用户 ID 最多 128 个字符；文件名过长时，请填写一个较短的用户 ID。")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise IdentityError("用户 ID 不能包含换行或控制字符。")
    return value, "provided" if explicit else "filename"


def ranking_scope(config, channels, category, data_fingerprint):
    scope = dict(mission_id=config["mission_id"], version=config["version"], source=config["source"],
        category_mode=category, selected_categories=CATEGORIES[category], start=config["start"], end=config["end"],
        target_channels=sorted(channels), evaluation_protocol=config.get("evaluation_protocol", PROTOCOL),
        aggregation=config.get("aggregation", "channel_macro_mean"),
        beta=config.get("beta", 0.5), null_policy="exclude_null_per_metric", data_fingerprint=data_fingerprint)
    encoded = json.dumps(scope, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return dict(key=hashlib.sha256(encoded).hexdigest(), config=scope)


class LeaderboardStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "leaderboard.json"

    @contextmanager
    def _locked(self):
        with _FILE_LOCK:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / ".leaderboard.lock").open("a+b") as lock:
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    lock.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return {"schema_version": 1, "submissions": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("submissions"), list):
                raise ValueError("格式或版本无效")
            for row in data["submissions"]:
                if not isinstance(row, dict) or not {"submission_id", "sequence", "scope_key", "scope", "user_id", "score", "submitted_at", "eligible", "filename", "input_mode", "id_source"}.issubset(row):
                    raise ValueError("提交记录缺少字段")
                if not isinstance(row["user_id"], str) or not row["user_id"] or not isinstance(row["submitted_at"], str) or not isinstance(row["scope_key"], str) or type(row["sequence"]) is not int or type(row["eligible"]) is not bool:
                    raise ValueError("提交记录类型无效")
                if not isinstance(row["scope"], dict) or not {"mission_id", "version", "source", "category_mode", "selected_categories", "start", "end", "target_channels", "evaluation_protocol", "aggregation", "data_fingerprint"}.issubset(row["scope"]):
                    raise ValueError("评测分组信息缺少字段")
                scope = row["scope"]
                if not all(isinstance(scope[k], str) for k in ("mission_id", "version", "source", "category_mode", "start", "end", "evaluation_protocol", "aggregation", "data_fingerprint")) or not all(isinstance(scope[k], list) and all(isinstance(v, str) for v in scope[k]) for k in ("selected_categories", "target_channels")):
                    raise ValueError("评测分组信息类型无效")
                moment = datetime.fromisoformat(row["submitted_at"].replace("Z", "+00:00"))
                if moment.tzinfo is None or moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") != row["submitted_at"]:
                    raise ValueError("提交时间须为规范 UTC 时间")
                if row["eligible"] != (row["score"] is not None):
                    raise ValueError("总评分与可排名状态不一致")
                if row["score"] is not None and (not isinstance(row["score"], (float, int)) or not math.isfinite(row["score"]) or not 0 <= row["score"] <= 1):
                    raise ValueError("总评分无效")
            return data
        except (ValueError, UnicodeError) as exc:
            raise LeaderboardError(f"排行榜存储文件损坏或不兼容：{self.path}；请备份并检查，系统不会覆盖原文件。{exc}") from exc

    def _write(self, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".leaderboard-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _board(data):
        submissions = data["submissions"]
        best = {}
        for row in submissions:
            if not row["eligible"]:
                continue
            old = best.get(row["user_id"])
            ordering = lambda r: (-r["score"], r["submitted_at"], r["sequence"])
            if old is None or ordering(row) < ordering(old):
                best[row["user_id"]] = row
        entries = sorted(best.values(), key=lambda r: (-r["score"], r["submitted_at"], r["user_id"]))
        return dict(key="unified",
                    entries=[dict(r, rank=i+1) for i,r in enumerate(entries)],
                    total_submissions=len(submissions), ranked_submissions=sum(r["eligible"] for r in submissions))

    def board(self):
        with self._locked():
            return self._board(self._read())

    def record(self, result, user_id, id_source, filename, mode, submitted_at=None):
        score = result["metrics"]["f0_5"]
        if score is not None and (not math.isfinite(score) or not 0 <= score <= 1):
            raise LeaderboardError("总评分无效，不能保存排行榜。")
        scope = ranking_scope(result["config"], result["config"]["target_channels"], result["config"]["category_mode"], result["data_fingerprint"])
        with self._locked():
            data = self._read()
            moment = datetime.fromisoformat(submitted_at.replace("Z", "+00:00")) if submitted_at else datetime.now(timezone.utc)
            if moment.tzinfo is None:
                raise LeaderboardError("提交时间必须带时区")
            now = moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            row = dict(submission_id=uuid4().hex, sequence=len(data["submissions"])+1, scope_key=scope["key"],
                scope=scope["config"], user_id=user_id, id_source=id_source, filename=filename, input_mode=mode,
                score=score, eligible=score is not None, submitted_at=now)
            data["submissions"].append(row)
            self._write(data)
            board = self._board(data)
            best = next((r for r in board["entries"] if r["user_id"] == user_id), None)
            return dict(submission_id=row["submission_id"], user_id=user_id, id_source=id_source, filename=filename,
                input_mode=mode, submitted_at=now, score=score, persisted=True, eligible=row["eligible"],
                scope_key=scope["key"], best_updated=best is not None and best["submission_id"] == row["submission_id"],
                rank=best["rank"] if best else None, best_score=best["score"] if best else None,
                best_submitted_at=best["submitted_at"] if best else None)
