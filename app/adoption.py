"""编码核定与文件采信。

职责边界：

* 试读（``trial``）只回答"哪些编码能完整读完、各自读成什么"，其中的档号
  仅以 ``trial_archive_ids`` 出现，永远不构成正式编号。
* 核定（``confirm``）记录复核员选定的编码。改判会立即把旧的采信结果标成
  ``STALE``，随后用新编码重算并原子发布，中间不存在新旧字段混用的窗口。
* 采信（``adopt``）产出正式的行与值。同一来源、同一核定重试只返回原来的
  采信结果，不重复生成正式行。
* 字段保形只允许读取某一次采信的快照，见 ``preserve_adoption``。

所有正式结果都带 ``adoption_id`` / ``generation`` / ``confirmation_id``，
便于下游核对"这些字段是不是同一次采信的"。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.encoding_probe import (
    canonical_encoding,
    candidates as decode_candidates,
    inspect,
)
from app.errors import DomainError
from app.manifest import canonical_digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_digest TEXT PRIMARY KEY,
    blob BLOB NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    encoding TEXT,
    confirmation_id TEXT,
    rule_version TEXT,
    state TEXT NOT NULL DEFAULT 'TRIAL',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmations (
    confirmation_id TEXT PRIMARY KEY,
    source_digest TEXT NOT NULL,
    seq INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (source_digest, seq)
);
CREATE TABLE IF NOT EXISTS adoptions (
    adoption_id TEXT PRIMARY KEY,
    source_digest TEXT NOT NULL,
    seq INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (source_digest, seq)
);
CREATE INDEX IF NOT EXISTS adoptions_source ON adoptions(source_digest);
"""

TRIAL = "TRIAL"
CONFIRMED = "CONFIRMED"
RECOMPUTING = "RECOMPUTING"
READY = "READY"
STALE = "STALE"
SUPERSEDED = "SUPERSEDED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_DEFAULT_STORES: dict[str, AdoptionStore] = {}
_DEFAULT_LOCK = threading.Lock()


def default_store(path: str | None = None, rule_version: str | None = None) -> "AdoptionStore":
    """进程内共享的默认采信库；显式传入路径时按路径复用。"""
    target = path or os.getenv("DB_PATH", "./data/archivecodec.db")
    with _DEFAULT_LOCK:
        store = _DEFAULT_STORES.get(target)
        if store is None:
            store = AdoptionStore(target, rule_version or "2026.11")
            _DEFAULT_STORES[target] = store
        return store


class AdoptionStore:
    """采信/核定状态库。与运行摘要库共用 SQLite 文件，互不干扰。"""

    def __init__(self, path: str, rule_version: str = "2026.11"):
        self.path = path
        self.rule_version = rule_version
        self._lock = threading.RLock()
        self._computing: set[str] = set()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # -- 读 ---------------------------------------------------------------
    def source(self, digest: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE source_digest=?", (digest,)).fetchone()
        return dict(row) if row else None

    def confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM confirmations WHERE confirmation_id=?", (confirmation_id,)
            ).fetchone()
        return dict(row) if row else None

    def source_by_prefix(self, prefix: str) -> dict[str, Any] | None:
        """按试读编号前缀找回来源；前缀不唯一时视为无效。"""
        prefix = str(prefix or "").strip()
        if prefix.startswith("trial-"):
            prefix = prefix[len("trial-"):]
        if not prefix:
            return None
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM sources WHERE source_digest LIKE ? LIMIT 2",
                (prefix + "%",),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    def adoption_row(self, digest: str, seq: int | None = None) -> dict[str, Any] | None:
        with self.connect() as db:
            if seq is None:
                source = db.execute(
                    "SELECT seq FROM sources WHERE source_digest=?", (digest,)
                ).fetchone()
                if not source:
                    return None
                seq = source["seq"]
            row = db.execute(
                "SELECT * FROM adoptions WHERE source_digest=? AND seq=?", (digest, seq)
            ).fetchone()
        return dict(row) if row else None

    def adoption_by_id(self, adoption_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM adoptions WHERE adoption_id=?", (adoption_id,)
            ).fetchone()
        return dict(row) if row else None

    def has_pending(self) -> bool:
        """是否存在尚未发布正式采信的核定（首次核定或改判重算中）。"""
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM sources WHERE state IN (?, ?) LIMIT 1",
                (CONFIRMED, RECOMPUTING),
            ).fetchone()
        return row is not None

    def export_target(self) -> tuple[dict[str, Any] | None, bool]:
        """默认导出目标：最近一次已发布采信；若更新的核定仍未完成则返回 pending。"""
        with self.connect() as db:
            ready = db.execute(
                "SELECT a.*, s.updated_at AS source_updated_at, s.rowid AS source_rowid "
                "FROM adoptions a JOIN sources s "
                "ON s.source_digest=a.source_digest AND s.seq=a.seq "
                "WHERE a.status='READY' AND s.state='READY' "
                "ORDER BY s.updated_at DESC, s.rowid DESC LIMIT 1"
            ).fetchone()
            pending = db.execute(
                "SELECT updated_at, rowid AS source_rowid FROM sources "
                "WHERE state IN (?, ?) ORDER BY updated_at DESC, rowid DESC LIMIT 1",
                (CONFIRMED, RECOMPUTING),
            ).fetchone()
        if pending is not None:
            if ready is None:
                return None, True
            if (pending["updated_at"], pending["source_rowid"]) > (
                ready["source_updated_at"], ready["source_rowid"]
            ):
                return None, True
        return (dict(ready) if ready else None), False

    def latest_ready_adoption(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.* FROM adoptions a JOIN sources s "
                "ON s.source_digest=a.source_digest AND s.seq=a.seq "
                "WHERE a.status='READY' AND s.state='READY' "
                "ORDER BY s.updated_at DESC, s.rowid DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- 写 ---------------------------------------------------------------
    def save_source(self, blob: bytes) -> str:
        digest = sha256(blob).hexdigest()
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO sources(source_digest, blob, state, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (digest, sqlite3.Binary(blob), TRIAL, _now()),
            )
        return digest


# ---------------------------------------------------------------------------
# 试读
# ---------------------------------------------------------------------------
def trial(blob: bytes, store: AdoptionStore | None = None) -> dict[str, Any]:
    """只读试读；返回的档号字段均为 trial 语义，不含正式编号。"""
    if not isinstance(blob, (bytes, bytearray)):
        raise DomainError("INVALID_SOURCE", "试读输入必须是字节")
    blob = bytes(blob)
    result = inspect(blob)
    result["trial_id"] = "trial-" + sha256(blob).hexdigest()[:12]
    if store is not None:
        store.save_source(blob)
    return result


# ---------------------------------------------------------------------------
# 核定
# ---------------------------------------------------------------------------
def _candidate_encodings(blob: bytes) -> set[str]:
    return {item["encoding"] for item in decode_candidates(blob)}


def _resolve_blob(
    store: AdoptionStore, blob: bytes | None, source_digest: str | None
) -> tuple[bytes, str]:
    if source_digest and store.source(source_digest) is None:
        found = store.source_by_prefix(source_digest)
        if found is not None:
            source_digest = found["source_digest"]
    if blob is not None:
        blob = bytes(blob)
        digest = sha256(blob).hexdigest()
        if source_digest and source_digest != digest:
            raise DomainError("SOURCE_MISMATCH", "来源摘要与提交字节不一致")
        return blob, digest
    if not source_digest:
        raise DomainError("UNKNOWN_SOURCE", "缺少来源摘要")
    source = store.source(source_digest)
    if source is None:
        raise DomainError("UNKNOWN_SOURCE", "没有该来源的试读记录")
    return bytes(source["blob"]), source_digest


def confirm(
    store: AdoptionStore,
    *,
    blob: bytes | None = None,
    source_digest: str | None = None,
    encoding: str,
    compute: bool = True,
    note: str | None = None,
    rule_version: str | None = None,
) -> dict[str, Any]:
    """核定编码。同一次核定重试是幂等的；改判会作废旧采信并重算。"""
    rule = rule_version or store.rule_version
    if isinstance(encoding, dict):
        encoding = encoding.get("encoding") or encoding.get("value") or encoding.get("name")
    if encoding is None:
        raise DomainError("UNSUPPORTED_ENCODING", "缺少核定的编码")
    enc = canonical_encoding(encoding)
    if enc is None:
        raise DomainError("UNSUPPORTED_ENCODING", f"不支持的编码：{encoding}")

    blob, digest = _resolve_blob(store, blob, source_digest)
    if enc not in _candidate_encodings(blob):
        raise DomainError("ENCODING_NOT_CANDIDATE", f"{enc} 无法完整读完该文件，不能核定")
    store.save_source(blob)

    stale_adoption_id: str | None = None
    idempotent = False
    in_progress = False
    with store.transaction() as db:
        row = db.execute("SELECT * FROM sources WHERE source_digest=?", (digest,)).fetchone()
        same = (
            row is not None
            and row["confirmation_id"] is not None
            and row["encoding"] == enc
            and (row["rule_version"] or "") == rule
            and row["state"] in (CONFIRMED, RECOMPUTING, READY)
        )
        if same:
            idempotent = True
            confirmation_id = row["confirmation_id"]
            seq = row["seq"]
            in_progress = row["state"] == RECOMPUTING
        else:
            seq = (row["seq"] if row is not None else 0) + 1
            # 改判：旧采信结果一律作废，且不会被任何下游继续读到。
            old = db.execute(
                "SELECT adoption_id FROM adoptions WHERE source_digest=? AND status='READY'",
                (digest,),
            ).fetchall()
            stale_adoption_id = old[0]["adoption_id"] if old else None
            db.execute(
                "UPDATE adoptions SET status=? WHERE source_digest=? AND status='READY'",
                (STALE, digest),
            )
            confirmation_id = "conf-" + canonical_digest(
                {
                    "source_digest": digest,
                    "seq": seq,
                    "encoding": enc,
                    "rule_version": rule,
                }
            )[:12]
            db.execute(
                "INSERT OR IGNORE INTO confirmations"
                "(confirmation_id, source_digest, seq, encoding, rule_version, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (confirmation_id, digest, seq, enc, rule, note, _now()),
            )
            db.execute(
                "UPDATE sources SET seq=?, encoding=?, confirmation_id=?, rule_version=?, "
                "state=?, updated_at=? WHERE source_digest=?",
                (seq, enc, confirmation_id, rule, CONFIRMED, _now(), digest),
            )

    adoption_result: dict[str, Any] | None = None
    if compute:
        # 即使上次在重算中途中断，也允许按同一次核定续算（结果幂等）。
        adoption_result = adopt(store, source_digest=digest, confirmation_id=confirmation_id)

    result: dict[str, Any] = {
        "status": CONFIRMED,
        "stage": CONFIRMED,
        "formal": False,
        "source_digest": digest,
        "encoding": enc,
        "confirmation_id": confirmation_id,
        "generation": seq,
        "adoption_seq": seq,
        "rule_version": rule,
        "idempotent": idempotent,
        "in_progress": in_progress,
        "requires_adoption": adoption_result is None,
        "requires_recompute": stale_adoption_id is not None,
        "stale_adoption_id": stale_adoption_id,
        "reason": "OK",
    }
    if adoption_result is not None:
        result["adoption"] = adoption_result
        for key in (
            "adoption_id",
            "archive_ids",
            "rows",
            "header",
            "input_count",
            "valid_count",
            "quarantine_count",
            "empty_count",
        ):
            result[key] = adoption_result[key]
        result["formal"] = True
        result["status"] = READY
        result["stage"] = "ADOPTED"
        result["idempotent"] = idempotent or adoption_result.get("idempotent", False)
    return result


# ---------------------------------------------------------------------------
# 采信
# ---------------------------------------------------------------------------
def _archive_key(header: list[str]) -> str | None:
    for key in header:
        if "档号" in str(key):
            return key
    for key in header:
        if str(key).strip().lower() in {"archive_id", "archive_no", "archiveid"}:
            return key
    return header[0] if header else None


def _build_adoption(
    digest: str,
    seq: int,
    encoding: str,
    confirmation_id: str,
    rule: str,
    blob: bytes,
) -> dict[str, Any]:
    text = blob.decode(encoding, "strict")
    if "�" in text:
        raise DomainError("DECODE_FAILED", "解码结果含替换字符")
    from app.modules.m1_intake.core import scan  # 延迟导入，避免循环依赖
    parsed = scan(text)
    header = [str(item) for item in (parsed["header"] or [])]
    key = _archive_key(header)
    adoption_id = "adopt-" + canonical_digest(
        {
            "source_digest": digest,
            "seq": seq,
            "confirmation_id": confirmation_id,
            "rule_version": rule,
        }
    )[:12]
    rows: list[dict[str, Any]] = []
    for parsed_row in parsed["rows"]:
        fields = {name: str(parsed_row[name]) for name in header if name in parsed_row}
        archive_id = fields.get(key) if key is not None else None
        rows.append(
            {
                "source_line": parsed_row["source_line"],
                "archive_id": archive_id,
                "fields": fields,
                "values": fields,
                "adoption_id": adoption_id,
                "generation": seq,
                "source_digest": digest,
                "encoding": encoding,
                "confirmation_id": confirmation_id,
                "formal": True,
                "stage": "ADOPTED",
            }
        )
    return {
        "adoption_id": adoption_id,
        "source_digest": digest,
        "generation": seq,
        "adoption_seq": seq,
        "encoding": encoding,
        "confirmation_id": confirmation_id,
        "rule_version": rule,
        "header": header,
        "archive_field": key,
        "rows": rows,
        "archive_ids": [item["archive_id"] for item in rows],
        "input_count": len(rows) + len(parsed["quarantine"]) + parsed["empty"],
        "valid_count": len(rows),
        "quarantine_count": len(parsed["quarantine"]),
        "empty_count": parsed["empty"],
        "quarantine": parsed["quarantine"],
        "reason": "OK" if not parsed["quarantine"] else parsed["quarantine"][0]["reason"],
    }


def _adoption_result(row: dict[str, Any], idempotent: bool) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    result = {
        "status": READY,
        "stage": "ADOPTED",
        "formal": True,
        "adoption_id": row["adoption_id"],
        "source_digest": row["source_digest"],
        "generation": row["seq"],
        "adoption_seq": row["seq"],
        "encoding": row["encoding"],
        "rule_version": row["rule_version"],
        "idempotent": idempotent,
        "served": True,
        **payload,
    }
    # payload 内的同名字段以行内记录为准，避免出现两份不一致的元数据。
    result["adoption_id"] = row["adoption_id"]
    result["source_digest"] = row["source_digest"]
    result["generation"] = row["seq"]
    result["adoption_seq"] = row["seq"]
    result["encoding"] = row["encoding"]
    result["rule_version"] = row["rule_version"]
    return result


def adopt(
    store: AdoptionStore,
    *,
    source_digest: str,
    confirmation_id: str | None = None,
    rule_version: str | None = None,
) -> dict[str, Any]:
    """按当前核定的编码生成正式采信结果；同一次核定重试不重复生成。"""
    rule = rule_version or store.rule_version
    source = store.source(source_digest)
    if source is None:
        raise DomainError("UNKNOWN_SOURCE", "没有该来源的试读记录")
    if not source["confirmation_id"]:
        raise DomainError("ENCODING_NOT_CONFIRMED", "编码尚未核定，不能生成正式采信结果")
    if confirmation_id is not None and confirmation_id != source["confirmation_id"]:
        raise DomainError("STALE_VERSION", "该核定已被改判，请按当前核定重新采信")
    if (source["rule_version"] or "") != rule:
        raise DomainError("STALE_VERSION", "该核定使用的规则版本已过期")

    seq = source["seq"]
    existing = store.adoption_row(source_digest, seq)
    if (
        existing is not None
        and existing["status"] == READY
        and existing["encoding"] == source["encoding"]
        and existing["rule_version"] == rule
    ):
        return _adoption_result(existing, idempotent=True)

    key = f"{source_digest}:{seq}"
    with store._lock:
        existing = store.adoption_row(source_digest, seq)
        if (
            existing is not None
            and existing["status"] == READY
            and existing["encoding"] == source["encoding"]
            and existing["rule_version"] == rule
        ):
            return _adoption_result(existing, idempotent=True)
        if key in store._computing:
            raise DomainError("ADOPTION_IN_PROGRESS", "同一核定的采信正在重算，请稍后重试")

        # 先公开进入重算状态：在这之前旧结果已标 STALE，在这之后新结果还没发布，
        # 因此下游只可能拿到"过期"或"完成"，不会拿到新旧混合的字段。
        with store.transaction() as db:
            current = db.execute(
                "SELECT * FROM sources WHERE source_digest=?", (source_digest,)
            ).fetchone()
            if (
                current is None
                or current["seq"] != seq
                or current["confirmation_id"] != source["confirmation_id"]
            ):
                raise DomainError("STALE_VERSION", "采信状态已变化，请刷新后重试")
            db.execute(
                "UPDATE sources SET state=?, updated_at=? WHERE source_digest=?",
                (RECOMPUTING, _now(), source_digest),
            )

        store._computing.add(key)
        try:
            built = _build_adoption(
                source_digest,
                seq,
                source["encoding"],
                source["confirmation_id"],
                rule,
                bytes(source["blob"]),
            )
            with store.transaction() as db:
                current = db.execute(
                    "SELECT * FROM sources WHERE source_digest=?", (source_digest,)
                ).fetchone()
                if (
                    current is None
                    or current["seq"] != seq
                    or current["confirmation_id"] != source["confirmation_id"]
                ):
                    raise DomainError("STALE_VERSION", "采信期间核定已改判，请重新采信")
                row = db.execute(
                    "SELECT * FROM adoptions WHERE source_digest=? AND seq=?",
                    (source_digest, seq),
                ).fetchone()
                if row is not None and row["status"] == READY:
                    return _adoption_result(dict(row), idempotent=True)
                payload = {
                    item: built[item]
                    for item in (
                        "header",
                        "archive_field",
                        "rows",
                        "archive_ids",
                        "input_count",
                        "valid_count",
                        "quarantine_count",
                        "empty_count",
                        "quarantine",
                        "reason",
                    )
                }
                db.execute(
                    "INSERT INTO adoptions(adoption_id, source_digest, seq, encoding, "
                    "rule_version, status, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_digest, seq) DO UPDATE SET "
                    "adoption_id=excluded.adoption_id, encoding=excluded.encoding, "
                    "rule_version=excluded.rule_version, status=excluded.status, "
                    "payload=excluded.payload, created_at=excluded.created_at",
                    (
                        built["adoption_id"],
                        source_digest,
                        seq,
                        source["encoding"],
                        rule,
                        READY,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        _now(),
                    ),
                )
                db.execute(
                    "UPDATE sources SET state=?, updated_at=? WHERE source_digest=?",
                    (READY, _now(), source_digest),
                )
            result = {
                "status": READY,
                "stage": "ADOPTED",
                "formal": True,
                "idempotent": False,
                "served": True,
                **built,
            }
            return result
        except Exception:
            # 重算失败也不能回到"可读"状态：旧结果仍是 STALE，新结果没有发布。
            with store.transaction() as db:
                db.execute(
                    "UPDATE sources SET state=?, updated_at=? WHERE source_digest=? AND seq=?",
                    (CONFIRMED, _now(), source_digest, seq),
                )
            raise
        finally:
            store._computing.discard(key)


# ---------------------------------------------------------------------------
# 下游读取
# ---------------------------------------------------------------------------
def current_adoption(
    store: AdoptionStore,
    *,
    source_digest: str | None = None,
    adoption_id: str | None = None,
    rule_version: str | None = None,
) -> dict[str, Any]:
    """取当前有效的正式采信快照。过期/重算中/改判后一律拒绝。"""
    rule = rule_version or store.rule_version
    row: dict[str, Any] | None = None
    if adoption_id:
        row = store.adoption_by_id(adoption_id)
        if row is None:
            raise DomainError("UNKNOWN_ADOPTION", "没有该采信结果")
        source_digest = row["source_digest"]
    if not source_digest:
        raise DomainError("UNKNOWN_SOURCE", "缺少来源摘要")
    source = store.source(source_digest)
    if source is None:
        raise DomainError("UNKNOWN_SOURCE", "没有该来源的试读记录")
    if not source["confirmation_id"]:
        raise DomainError("ENCODING_NOT_CONFIRMED", "编码尚未核定")
    if source["state"] == RECOMPUTING:
        raise DomainError("STALE_VERSION", "采信结果正在重算，完成前不下发字段")
    if source["state"] != READY:
        raise DomainError("STALE_VERSION", "采信结果尚未生成或已过期")
    if (source["rule_version"] or "") != rule:
        raise DomainError("STALE_VERSION", "采信结果使用的规则版本已过期")
    if row is None:
        row = store.adoption_row(source_digest, source["seq"])
    if row is None or row["status"] != READY:
        raise DomainError("STALE_VERSION", "采信结果已过期，请重新采信")
    if row["seq"] != source["seq"] or row["source_digest"] != source_digest:
        raise DomainError("STALE_VERSION", "采信结果不属于当前核定")
    if row["encoding"] != source["encoding"]:
        raise DomainError("STALE_VERSION", "采信结果与当前核定的编码不一致")
    if row["rule_version"] != rule:
        raise DomainError("STALE_VERSION", "采信结果使用的规则版本已过期")
    return _adoption_result(row, idempotent=False)


def ensure_single_adoption(
    rows: list[dict[str, Any]], expected: str | None = None
) -> str:
    """下游字段必须来自同一次采信；试读行和混用行一律拒绝。"""
    if not isinstance(rows, list):
        raise DomainError("MIXED_ADOPTION", "字段行必须是列表")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DomainError("MIXED_ADOPTION", "字段行形状不正确")
        if row.get("formal") is False or row.get("stage") == TRIAL:
            raise DomainError("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
        adoption_id = row.get("adoption_id")
        if adoption_id:
            ids.add(str(adoption_id))
        elif expected:
            raise DomainError("MIXED_ADOPTION", "字段行缺少采信编号")
    if len(ids) > 1:
        raise DomainError("MIXED_ADOPTION", "新旧采信的字段不能混用")
    if expected is not None and ids and ids != {expected}:
        raise DomainError("MIXED_ADOPTION", "字段行不属于指定的采信结果")
    if expected is not None and not ids:
        raise DomainError("MIXED_ADOPTION", "字段行缺少采信编号")
    return next(iter(ids), expected or "")


# 语义别名，方便调用方按中文术语查找。
run_trial = trial
run_confirm = confirm
run_adoption = adopt
