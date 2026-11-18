import csv, io

SAMPLE={"text":"档号,盒号,题名\n000123,07,年度 汇编\n档号,盒号,题名\n9007199254740993,08,会议\nBAD,09\n"}

# 旧路径的文本输入按 UTF-8 处理；字节输入必须走编码试读/核定。
DEFAULT_TEXT_ENCODING="utf-8"


def scan(text: str):
    rows=[]; quarantine=[]; empty=0; header=None
    reader=csv.reader(io.StringIO(text, newline=""))
    for line,row in enumerate(reader,1):
        if not row or not any(x.strip() for x in row): empty+=1; continue
        if header is None: header=[x.lstrip("﻿") for x in row]; continue
        if [x.lstrip("﻿") for x in row]==header: continue
        if len(row)!=len(header): quarantine.append({"source_line":line,"reason":"BAD_COLUMNS","raw":row}); continue
        rows.append(dict(zip(header,row))|{"source_line":line})
    return {"rows":rows,"quarantine":quarantine,"empty":empty,"header":header}


def _accounting(out):
    return {
        "input_count":len(out["rows"])+len(out["quarantine"])+out["empty"],
        "valid_count":len(out["rows"]),
        "quarantine_count":len(out["quarantine"]),
        "empty_count":out["empty"],
        "header":out["header"],
        "reason":out["quarantine"][0]["reason"] if out["quarantine"] else "OK",
    }


def _blob(payload):
    """取出原始字节；文本输入按 UTF-8 编码。"""
    if "hex" in payload and payload["hex"] is not None:
        return bytes.fromhex(str(payload["hex"]))
    if "bytes" in payload and payload["bytes"] is not None:
        value=payload["bytes"]
        return bytes(value) if not isinstance(value,str) else bytes.fromhex(value)
    if "data" in payload and payload["data"] is not None:
        value=payload["data"]
        return bytes(value) if not isinstance(value,str) else bytes.fromhex(value)
    if payload.get("base64") is not None:
        import base64
        return base64.b64decode(str(payload["base64"]))
    text=payload.get("text",SAMPLE["text"])
    return str(text).encode(payload.get("text_encoding") or DEFAULT_TEXT_ENCODING)


def _archive_key(header):
    if not header: return None
    for key in header:
        if "档号" in str(key): return key
    return header[0]


def _trial_view(blob, technical=None, store=None):
    """试读结果：只给 trial 档号，刻意不给正式 archive_ids。"""
    from app.adoption import trial as _adoption_trial
    probe=_adoption_trial(blob, store)
    best_text=probe.get("text","")
    out=scan(best_text)
    key=_archive_key(out["header"])
    trial_ids=[str(row[key]) for row in out["rows"] if key is not None and key in row]
    result={
        # 解析本身是 READY，但这是试读：stage/formal 标记它不能当正式编号用。
        "status":"READY","stage":"TRIAL","formal":False,"trial_status":"TRIAL",
        "trial_id":probe.get("trial_id") or ("trial-"+probe["source_digest"][:12]),
        "source_digest":probe["source_digest"],
        "encoding":probe.get("encoding"),
        "score":probe.get("score"),
        "ambiguous":probe.get("ambiguous",False),
        "trial_reason":probe.get("reason"),
        "requires_decision":probe.get("requires_decision",False),
        "requires_confirmation":probe.get("requires_confirmation",False),
        "candidates":probe.get("candidates",[]),
        "trial_archive_ids":trial_ids,
        "preview_archive_ids":list(trial_ids),
        # 试读档号不是正式编号，下游拿到 None 就不能当编号用。
        "archive_ids":None,
        "formal_archive_ids":None,
        "archive_field":key,
        **_accounting(out),
    }
    # 歧义优先于单次解析的原因码：复核员需要看到"必须核定"。
    if probe.get("reason") in {"ENCODING_AMBIGUOUS","DECODE_FAILED"}:
        result["reason"]=probe["reason"]
    if technical is not None:
        result["technical"]=technical
    return result


def _adoption_view(adoption, technical=None):
    """把正式采信结果整理成文件采信页面的形状。"""
    result={
        "status":"READY","stage":"ADOPTED","formal":True,
        "adoption_id":adoption["adoption_id"],
        "source_digest":adoption["source_digest"],
        "generation":adoption["generation"],
        "adoption_seq":adoption.get("adoption_seq",adoption["generation"]),
        "encoding":adoption["encoding"],
        "confirmation_id":adoption.get("confirmation_id"),
        "rule_version":adoption.get("rule_version"),
        "header":adoption.get("header",[]),
        "archive_field":adoption.get("archive_field"),
        "archive_ids":adoption.get("archive_ids",[]),
        "rows":adoption.get("rows",[]),
        "input_count":adoption.get("input_count",0),
        "valid_count":adoption.get("valid_count",0),
        "quarantine_count":adoption.get("quarantine_count",0),
        "empty_count":adoption.get("empty_count",0),
        "reason":adoption.get("reason","OK"),
        "idempotent":adoption.get("idempotent",False),
    }
    if technical is not None:
        result["technical"]=technical
    return result


def _store(store):
    if store is not None: return store
    from app.adoption import default_store
    return default_store()


def trial(payload=None, store=None):
    """试读（不产生正式采信结果）。"""
    payload=payload or {}
    blob=_blob(payload)
    return _trial_view(blob, store=store)


def confirm(payload=None, store=None):
    """核定编码，并让文件采信随核定更新。"""
    from app.adoption import confirm as _confirm
    payload=dict(payload or {})
    encoding=(payload.get("encoding") or payload.get("confirmed_encoding")
              or payload.get("chosen_encoding") or payload.get("selected_encoding")
              or payload.get("decision_encoding") or payload.get("confirm")
              or payload.get("decision"))
    if encoding is None:
        raise ValueError("UNKNOWN_ENCODING")
    source_digest=payload.get("source_digest") or payload.get("digest")
    byte_keys=("hex","bytes","data","source_hex","text")
    blob=_blob(payload) if any(key in payload for key in byte_keys) else None
    if blob is None and source_digest is None:
        blob=_blob(payload)
    return _confirm(
        _store(store), blob=blob, source_digest=source_digest, encoding=encoding,
        compute=payload.get("compute",not payload.get("defer_recompute",False)),
        note=payload.get("note"),
        rule_version=payload.get("rule_version"),
    )


def adopt(payload=None, store=None):
    """按当前核定生成正式采信结果；同一次核定重试复用原结果。"""
    from app.adoption import adopt as _adopt
    payload=dict(payload or {})
    source_digest=payload.get("source_digest") or payload.get("digest")
    if source_digest and not payload.get("adoption_id"):
        return _adoption_view(_adopt(
            _store(store), source_digest=source_digest,
            confirmation_id=payload.get("confirmation_id"),
            rule_version=payload.get("rule_version"),
        ))
    if payload.get("adoption_id"):
        from app.adoption import current_adoption
        return _adoption_view(current_adoption(
            _store(store), adoption_id=payload.get("adoption_id"),
            rule_version=payload.get("rule_version"),
        ))
    confirmed=confirm(payload, store)
    return _adoption_view(confirmed["adoption"] if "adoption" in confirmed else confirmed)


def _latest_adoption(store):
    """文件采信随核定更新：没有显式输入时读取最近一次已发布的正式结果。"""
    if store is None:
        return None
    row = store.latest_ready_adoption()
    if row is None:
        return None
    from app.adoption import _adoption_result
    return _adoption_result(row, idempotent=False)


def run(payload=None, store=None):
    payload=payload or {}
    if not isinstance(payload,dict):
        payload={"bytes":list(payload)}
    action=str(payload.get("action") or payload.get("op") or "").lower()
    explicit = action or any(
        key in payload
        for key in ("text","hex","bytes","data","base64","source_hex","confirm",
                    "encoding","confirmed_encoding","source_digest","adoption_id")
    )
    if action in {"trial","probe","preview","read"}:
        return _trial_view(_blob(payload), store=store)
    if not explicit:
        current = _latest_adoption(store)
        if current is not None:
            return _adoption_view(current)
    if action in {"confirm","decide","verify","approve"} or payload.get("confirm") \
            or payload.get("encoding") or payload.get("confirmed_encoding"):
        confirmed=confirm(payload, store)
        adoption=confirmed.get("adoption") if isinstance(confirmed, dict) else None
        if not isinstance(adoption, dict):
            # 只核定了编码、尚未采信：明确告诉调用方还没正式结果。
            return {"status":"CONFIRMED","stage":"CONFIRMED","formal":False,
                    **confirmed,"adoption":None}
        return _adoption_view(adoption, technical=confirmed)
    if action in {"adopt","adoption"} or payload.get("adopt") \
            or payload.get("source_digest") or payload.get("adoption_id"):
        return adopt(payload, store)
    return _trial_view(_blob(payload), store=store)
