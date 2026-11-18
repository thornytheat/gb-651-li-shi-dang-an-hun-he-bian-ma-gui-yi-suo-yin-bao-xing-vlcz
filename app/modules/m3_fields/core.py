from decimal import Decimal, InvalidOperation

SAMPLE={"values":["000123","9007199254740993","N/A"," 07 "]}
NULLS={"","-","N/A","NULL","未知"}


def preserve(value):
    if value is None: return {"state":"null","value":None}
    raw=str(value); stripped=raw.strip()
    if stripped in NULLS: return {"state":"sentinel","value":None,"raw":raw}
    return {"state":"value","value":stripped,"raw":raw}


def decimal_text(value):
    try: return format(Decimal(str(value).replace(",","")),"f")
    except InvalidOperation: raise ValueError("INVALID_VALUE")


def _fail(code, message):
    from app.errors import DomainError
    raise DomainError(code, message)


def _is_trial(value):
    return (
        not isinstance(value, dict)
        or value.get("formal") is False
        or value.get("stage") == "TRIAL"
        or value.get("trial") is not None
        or value.get("status") == "TRIAL"
    )


def _resolve_adoption(payload, store):
    """取出唯一一次采信快照；过期、试读、混用都在这里挡掉。"""
    from app.adoption import current_adoption, default_store

    if payload.get("trial") is not None:
        _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
    if payload.get("stage") == "TRIAL" or payload.get("status") == "TRIAL" \
            or payload.get("formal") is False:
        _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")

    adopted = payload.get("adoption") or payload.get("adoption_snapshot")
    if isinstance(adopted, dict):
        if _is_trial(adopted):
            _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
        adoption_id = adopted.get("adoption_id")
        if not adoption_id:
            _fail("MIXED_ADOPTION", "采信快照缺少采信编号")
        return current_adoption(
            store or default_store(), adoption_id=str(adoption_id),
            rule_version=payload.get("rule_version"),
        )

    adoption_id = payload.get("adoption_id")
    source_digest = payload.get("source_digest") or payload.get("source")
    if adoption_id or source_digest:
        return current_adoption(
            store or default_store(),
            adoption_id=str(adoption_id) if adoption_id else None,
            source_digest=str(source_digest) if source_digest else None,
            rule_version=payload.get("rule_version"),
        )
    return None


def preserve_adoption(adoption, *, rows=None, store=None):
    """只用同一次采信的行和值做字段保形。"""
    from app.adoption import ensure_single_adoption

    if _is_trial(adoption):
        _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
    if adoption.get("status") in {"STALE", "SUPERSEDED", "RECOMPUTING", "CONFIRMED"}:
        _fail("STALE_VERSION", "采信结果不是当前有效版本")
    adoption_id = adoption.get("adoption_id")
    if not adoption_id:
        _fail("MIXED_ADOPTION", "采信结果缺少采信编号")
    adoption_id = str(adoption_id)

    source_rows = adoption.get("rows")
    if not isinstance(source_rows, list):
        _fail("MIXED_ADOPTION", "采信结果缺少行")
    ensure_single_adoption(source_rows, adoption_id)

    if rows is not None:
        if not isinstance(rows, list):
            _fail("MIXED_ADOPTION", "字段行必须是列表")
        ensure_single_adoption(rows, adoption_id)
        chosen = []
        for row in rows:
            if not isinstance(row, dict):
                _fail("MIXED_ADOPTION", "字段行形状不正确")
            if _is_trial(row):
                _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
            if str(row.get("adoption_id")) != adoption_id:
                _fail("MIXED_ADOPTION", "新旧采信的字段不能混用")
            original = next(
                (item for item in source_rows
                 if item.get("source_line") == row.get("source_line")),
                None,
            )
            if original is None:
                _fail("MIXED_ADOPTION", "字段行不属于该次采信")
            submitted = row.get("fields") or row.get("values")
            if submitted is not None:
                source = original.get("fields") or original.get("values") or {}
                if submitted != source:
                    _fail("MIXED_ADOPTION", "字段值必须来自同一次采信")
            chosen.append(original)
    else:
        chosen = source_rows

    output_rows=[]; flat_values=[]
    for row in chosen:
        fields = row.get("fields") or row.get("values") or {}
        if not isinstance(fields, dict):
            _fail("MIXED_ADOPTION", "采信行字段形状不正确")
        preserved={}
        for name, raw in fields.items():
            state=preserve(raw); preserved[str(name)]=state; flat_values.append(state["value"])
        output_rows.append({
            "source_line": row.get("source_line"),
            "archive_id": row.get("archive_id"),
            "fields": preserved,
            "adoption_id": adoption_id,
            "generation": row.get("generation", adoption.get("generation")),
            "source_digest": row.get("source_digest", adoption.get("source_digest")),
            "encoding": row.get("encoding", adoption.get("encoding")),
            "confirmation_id": row.get("confirmation_id", adoption.get("confirmation_id")),
            "formal": True,
            "stage": "ADOPTED",
        })
    return {
        "status":"READY","stage":"ADOPTED","formal":True,
        "adoption_id":adoption_id,
        "source_digest":adoption.get("source_digest"),
        "generation":adoption.get("generation"),
        "adoption_seq":adoption.get("generation"),
        "encoding":adoption.get("encoding"),
        "confirmation_id":adoption.get("confirmation_id"),
        "rule_version":adoption.get("rule_version"),
        "input_count":len(output_rows),"valid_count":len(output_rows),"quarantine_count":0,
        "values":flat_values,"rows":output_rows,"reason":"OK",
    }


def _standalone_rows(rows, store):
    """提交行必须能对上库内同一次采信；试读行、未知编号一律拒绝。"""
    from app.adoption import current_adoption, ensure_single_adoption, default_store

    if not isinstance(rows, list) or not rows:
        _fail("MIXED_ADOPTION", "字段行必须是列表")
    for row in rows:
        if not isinstance(row, dict):
            _fail("MIXED_ADOPTION", "字段行形状不正确")
        if _is_trial(row) or not row.get("adoption_id"):
            _fail("MIXED_ADOPTION", "字段行缺少有效采信编号")
    adoption_id = ensure_single_adoption(rows, None)
    adoption = current_adoption(store or default_store(), adoption_id=str(adoption_id))
    return preserve_adoption(adoption, rows=rows)


def run(payload=None, store=None):
    payload = payload or {}
    if isinstance(payload, dict) and any(
        key in payload
        for key in ("adoption", "adoption_id", "adoption_snapshot", "source_digest", "trial")
    ):
        adoption = _resolve_adoption(payload, store)
        if adoption is not None:
            return preserve_adoption(adoption, rows=payload.get("rows"), store=store)
    if isinstance(payload, dict) and (payload.get("stage") == "TRIAL"
                                      or payload.get("status") == "TRIAL"
                                      or payload.get("formal") is False):
        _fail("TRIAL_NOT_FORMAL", "试读结果不能作为正式字段使用")
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return _standalone_rows(payload["rows"], store)
    values=payload.get("values",SAMPLE["values"]); out=[preserve(v) for v in values]
    return {"status":"READY","input_count":len(out),"valid_count":sum(x["state"]=="value" for x in out),"quarantine_count":0,"values":[x["value"] for x in out],"reason":"OK"}
