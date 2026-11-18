import csv, io
from app.manifest import canonical_digest, run_id
from app.errors import DomainError
SAMPLE={"store":None,"rows":[{"source_line":2,"archive_id":"000123","reason":"OK"},{"source_line":5,"archive_id":"BAD","reason":"BAD_COLUMNS"}]}


def csv_text(rows):
    out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=["source_line","archive_id","reason"],lineterminator="\n",extrasaction="ignore"); writer.writeheader(); writer.writerows(rows); return out.getvalue()


def _reject_trial(rows):
    """试读档号不能被当成正式编号导出。"""
    if not isinstance(rows, list):
        raise DomainError("MIXED_ADOPTION", "导出行必须是列表")
    ids=set()
    for row in rows:
        if not isinstance(row, dict):
            raise DomainError("MIXED_ADOPTION", "导出行形状不正确")
        if row.get("formal") is False or row.get("stage") == "TRIAL" or row.get("status") == "TRIAL":
            raise DomainError("TRIAL_NOT_FORMAL", "试读档号不能被当成正式编号导出")
        if row.get("adoption_id"):
            ids.add(str(row["adoption_id"]))
    if len(ids) > 1:
        raise DomainError("MIXED_ADOPTION", "新旧采信的行不能混在一次导出里")
    return ids


def _adoption_rows(adoption):
    rows=[]
    for row in adoption.get("rows", []):
        rows.append({
            "source_line": row.get("source_line"),
            "archive_id": row.get("archive_id"),
            "reason": "OK",
            "adoption_id": row.get("adoption_id"),
            "generation": row.get("generation"),
            "source_digest": row.get("source_digest"),
            "formal": True,
            "stage": "ADOPTED",
        })
    for item in adoption.get("quarantine", []) or []:
        rows.append({
            "source_line": item.get("source_line"),
            "archive_id": None,
            "reason": item.get("reason", "INVALID_VALUE"),
            "adoption_id": adoption.get("adoption_id"),
            "generation": adoption.get("generation"),
            "source_digest": adoption.get("source_digest"),
            "formal": True,
            "stage": "ADOPTED",
        })
    rows.sort(key=lambda row: (row["source_line"] is None, row["source_line"]))
    return rows


def _current_adoption_or_none(adoption_store):
    """导出默认使用最近一次已发布的正式采信；更新的核定未完成则返回 pending。"""
    if adoption_store is None:
        return None, False
    return adoption_store.export_target()


def _from_adoption(payload, adoption_store):
    from app.adoption import current_adoption, default_store

    adoption_store = adoption_store or default_store()
    adoption = payload.get("adoption")
    if isinstance(adoption, dict):
        adoption_id = adoption.get("adoption_id")
        if adoption_id:
            return current_adoption(
                adoption_store, adoption_id=str(adoption_id),
                rule_version=payload.get("rule_version"),
            )
        raise DomainError("MIXED_ADOPTION", "采信快照缺少采信编号")
    adoption_id = payload.get("adoption_id")
    source_digest = payload.get("source_digest") or payload.get("source")
    if adoption_id or source_digest:
        return current_adoption(
            adoption_store,
            adoption_id=str(adoption_id) if adoption_id else None,
            source_digest=str(source_digest) if source_digest else None,
            rule_version=payload.get("rule_version"),
        )
    return None


def run(payload=None, store=None, adoption_store=None):
    payload = payload or {}
    if store is None and isinstance(payload, dict):
        store = payload.get("store")
    adoption = _from_adoption(payload, adoption_store) if isinstance(payload, dict) else None
    if adoption is None and isinstance(payload, dict) and not payload.get("rows") \
            and not any(key in payload for key in ("adoption_id", "source_digest", "adoption")):
        row, pending = _current_adoption_or_none(adoption_store)
        if pending:
            # 有改判尚未重算完成：不导出，也不退回样例，避免新旧字段混用。
            raise DomainError("STALE_VERSION", "采信结果正在重算，完成前不导出")
        if row is not None:
            from app.adoption import _adoption_result
            adoption = _adoption_result(row, idempotent=False)
    if adoption is not None:
        formal_rows = _adoption_rows(adoption)
        rows = formal_rows
        digest = canonical_digest({"adoption_id": adoption["adoption_id"], "rows": formal_rows})
        rid = run_id({"adoption_id": adoption["adoption_id"], "rows": formal_rows})
    else:
        rows = payload.get("rows", SAMPLE["rows"]) if isinstance(payload, dict) else SAMPLE["rows"]
        _reject_trial(rows)
        digest = canonical_digest(rows)
        rid = run_id(rows)
    result={"input_count":len(rows),"valid_count":sum(r["reason"]=="OK" for r in rows),"quarantine_count":sum(r["reason"]!="OK" for r in rows)}
    out = {"status":"READY","run_id":rid,"digest":digest,"csv":csv_text(rows),**result,"reason":"OK"}
    if adoption is not None:
        out.update({
            "stage":"ADOPTED","formal":True,
            "adoption_id":adoption["adoption_id"],
            "source_digest":adoption["source_digest"],
            "generation":adoption["generation"],
            "encoding":adoption.get("encoding"),
            "rows":rows,
        })
    if store:
        rid=store.record(digest,rid,result)["run_id"]
        out["run_id"]=rid
    return out
