SAMPLE={"index_ids":["000123","9007199254740993","000404"],"normalized_ids":["000123","9007199254740993","000777"],"quarantine":1,"empty":0}

from app.errors import DomainError


def _reject_trial(data):
    """下游只能对账正式编号；试读档号一律拒绝。"""
    if not isinstance(data, dict):
        return
    if data.get("trial") is not None or data.get("stage") == "TRIAL" \
            or data.get("formal") is False:
        raise DomainError("TRIAL_NOT_FORMAL", "试读档号不能作为正式编号对账")
    rows = data.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and (
                row.get("formal") is False or row.get("stage") == "TRIAL"
            ):
                raise DomainError("TRIAL_NOT_FORMAL", "试读档号不能作为正式编号对账")
        ids = {str(row.get("adoption_id")) for row in rows
               if isinstance(row, dict) and row.get("adoption_id")}
        if len(ids) > 1:
            raise DomainError("MIXED_ADOPTION", "新旧采信的档号不能混用")


def _from_adoption(payload, store):
    from app.adoption import current_adoption, default_store

    adoption = payload.get("adoption")
    if not isinstance(adoption, dict):
        adoption_id = payload.get("adoption_id") or payload.get("adoption")
        source_digest = payload.get("source_digest") or payload.get("source")
        if not adoption_id and not source_digest:
            return None
        adoption = current_adoption(
            store or default_store(),
            adoption_id=str(adoption_id) if adoption_id else None,
            source_digest=str(source_digest) if source_digest else None,
            rule_version=payload.get("rule_version"),
        )
    else:
        adoption_id = adoption.get("adoption_id")
        if adoption_id:
            # 快照必须仍是当前有效采信，改判后旧快照不能继续对账。
            adoption = current_adoption(
                store or default_store(),
                adoption_id=str(adoption_id),
                rule_version=payload.get("rule_version"),
            )
    return adoption


def reconcile(index_ids, normalized_ids, quarantine=0, empty=0):
    left=set(map(str,index_ids)); right=set(map(str,normalized_ids)); missing=sorted(left-right); orphan=sorted(right-left)
    return {"index_count":len(index_ids),"output_count":len(normalized_ids),"missing":missing,"orphan":orphan,"quarantine_count":quarantine,"empty_count":empty,"balanced":len(normalized_ids)+quarantine+empty>=len(right)}


def run(payload=None, store=None):
    payload = payload or {}
    _reject_trial(payload)
    adoption = _from_adoption(payload, store)
    if adoption is not None:
        normalized_ids = [str(item) for item in adoption.get("archive_ids", [])]
        data = {
            "index_ids": payload.get("index_ids", SAMPLE["index_ids"]),
            "normalized_ids": normalized_ids,
            "quarantine": payload.get(
                "quarantine", adoption.get("quarantine_count", 0)
            ),
            "empty": payload.get("empty", adoption.get("empty_count", 0)),
        }
        out = reconcile(data["index_ids"], data["normalized_ids"], data["quarantine"], data["empty"])
        return {
            "status": "READY", "stage": "ADOPTED", "formal": True,
            "source_digest": adoption.get("source_digest"),
            "adoption_id": adoption.get("adoption_id"),
            "generation": adoption.get("generation"),
            "encoding": adoption.get("encoding"),
            "input_count": out["index_count"], "valid_count": out["output_count"],
            **out,
            "reason": "DIFF" if out["missing"] or out["orphan"] else "OK",
        }
    data=SAMPLE|payload; out=reconcile(data["index_ids"],data["normalized_ids"],data.get("quarantine",0),data.get("empty",0)); return {"status":"READY","input_count":out["index_count"],"valid_count":out["output_count"],**out,"reason":"DIFF" if out["missing"] or out["orphan"] else "OK"}
