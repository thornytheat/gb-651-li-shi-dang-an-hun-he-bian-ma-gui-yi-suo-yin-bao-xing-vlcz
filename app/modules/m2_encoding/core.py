SAMPLE={"hex":"e6a1a3e58fb7"}

from app.encoding_probe import BOM, ENCODINGS, inspect as _inspect


def _blob(payload) -> bytes:
    """从多种提交形状里取出原始字节。"""
    if payload is None:
        return bytes.fromhex(SAMPLE["hex"])
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if not isinstance(payload, dict):
        raise ValueError("INVALID_SOURCE")
    for key in ("hex", "source_hex"):
        if payload.get(key) is not None:
            return bytes.fromhex(str(payload[key]))
    for key in ("bytes", "data"):
        value = payload.get(key)
        if value is not None:
            return bytes(value) if not isinstance(value, str) else bytes.fromhex(value)
    if payload.get("text") is not None:
        name = str(payload.get("text_encoding") or "utf-8").strip().lower()
        encoding = {"utf8": "utf-8", "gb18030": "gb18030",
                    "gbk": "gb18030", "gb2312": "gb18030"}.get(name, "utf-8")
        return str(payload["text"]).encode(encoding)
    return bytes.fromhex(SAMPLE["hex"])


def detect(blob: bytes):
    """原有判定规则与返回形状保持不变，另附试读候选（不构成正式编号）。"""
    result = _detect(blob)
    try:
        probe = _inspect(blob)
    except Exception:
        probe = {}
    candidates = []
    for item in probe.get("candidates", []):
        candidates.append({k: v for k, v in item.items() if k != "_parsed"})
    trial_ids = [str(value) for value in probe.get("trial_archive_ids", []) or []]
    result.update({
        # 判定规则本身给出 READY；试读语义由 stage/formal 表达。
        "status": "READY",
        "stage": "TRIAL",
        "formal": False,
        "trial_status": "TRIAL",
        "ambiguous": probe.get("ambiguous", False),
        "trial_reason": probe.get("reason"),
        "requires_decision": probe.get("requires_decision", False),
        "requires_confirmation": probe.get("requires_confirmation", False),
        "source_digest": probe.get("source_digest"),
        "columns": probe.get("columns", 0),
        "candidates": candidates,
        # 试读档号：正式编号只能由核定后的采信产生。
        "trial_archive_ids": trial_ids,
        "preview_archive_ids": list(trial_ids),
        "formal_archive_ids": None,
        "archive_ids": None,
    })
    return result


def _detect(blob: bytes):
    if blob.startswith(BOM):
        text=blob.decode("utf-8-sig","strict")
        return {"encoding":"utf-8-sig","text":text,"score":sum(1 for c in text if "一"<=c<="鿿")*4,"reason":"OK"}
    candidates=[]
    for name in ENCODINGS:
        try:
            text=blob.decode(name,"strict")
            score=sum(1 for c in text if "一"<=c<="鿿")*4-sum(1 for c in text if ord(c)<32 and c not in "\r\n\t")*8
            candidates.append((score,name,text))
        except UnicodeDecodeError: pass
    if not candidates: return {"encoding":"REJECTED","text":"","reason":"DECODE_FAILED"}
    score,name,text=max(candidates,key=lambda x:(x[0],x[1]=="utf-8-sig"))
    return {"encoding":name,"text":text,"score":score,"reason":"OK"}


def trial(payload=None, store=None):
    """编码试读：列出可读候选，只给 trial 档号，不给正式编号。"""
    from app.adoption import trial as _trial
    return _trial(_blob(payload), store)


def confirm(payload=None, store=None):
    """核定编码；文件采信随核定更新（改判重算，同一次核定重试不重复生成）。"""
    from app.adoption import confirm as _confirm, default_store
    payload = payload or {}
    if not isinstance(payload, dict):
        raise ValueError("INVALID_SOURCE")
    encoding = (payload.get("encoding") or payload.get("confirm")
                or payload.get("confirmed_encoding") or payload.get("chosen_encoding")
                or payload.get("selected_encoding") or payload.get("decision_encoding")
                or payload.get("decision"))
    source_digest = payload.get("source_digest") or payload.get("digest")
    blob = None
    if any(key in payload for key in ("hex", "bytes", "data", "source_hex", "text")):
        blob = _blob(payload)
    elif source_digest is None:
        blob = _blob(payload)
    return _confirm(
        store or default_store(),
        blob=blob,
        source_digest=source_digest,
        encoding=encoding,
        compute=payload.get("compute", not payload.get("defer_recompute", False)),
        note=payload.get("note"),
        rule_version=payload.get("rule_version"),
    )


def adopt(payload=None, store=None):
    """按当前核定生成正式采信结果；同一次核定重试复用原结果。"""
    from app.adoption import adopt as _adopt, default_store
    payload = payload or {}
    if not isinstance(payload, dict):
        payload = {"bytes": list(payload)}
    source_digest = payload.get("source_digest") or payload.get("digest")
    if source_digest is None:
        confirmed = confirm(payload, store)
        return confirmed.get("adoption", confirmed)
    return _adopt(
        store or default_store(),
        source_digest=source_digest,
        confirmation_id=payload.get("confirmation_id"),
        rule_version=payload.get("rule_version"),
    )


def run(payload=None, store=None):
    payload = payload or {}
    if not isinstance(payload, dict):
        payload = {"bytes": list(payload)}
    action = str(payload.get("action") or payload.get("op") or "").lower()
    if action in {"trial", "probe", "preview", "read"}:
        return trial(payload, store)
    if action in {"confirm", "decide", "verify", "approve"} or "confirm" in payload:
        return confirm(payload, store)
    if action in {"adopt", "adoption"} or payload.get("adopt"):
        return adopt(payload, store)
    if payload.get("encoding"):
        return confirm(payload, store)
    if payload.get("source_digest") or payload.get("adoption_id"):
        return adopt(payload, store)
    raw=_blob(payload); out=detect(raw)
    return {"status":"READY","input_count":len(raw),"valid_count":1 if out["reason"]=="OK" else 0,"quarantine_count":0 if out["reason"]=="OK" else 1,**out}
