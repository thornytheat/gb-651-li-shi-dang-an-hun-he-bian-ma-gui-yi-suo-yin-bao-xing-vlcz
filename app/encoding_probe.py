"""编码试读。

试读只回答"这份字节用哪些编码能完整读完、各自会读成什么"，
不产生任何正式档号。正式档号只能由 ``app.adoption.confirm`` 在核定后生成。
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any


ENCODINGS = ("utf-8-sig", "gb18030")
BOM = b"\xef\xbb\xbf"

_ALIASES = {
    "utf-8": "utf-8-sig",
    "utf8": "utf-8-sig",
    "utf_8": "utf-8-sig",
    "utf-8-sig": "utf-8-sig",
    "utf8-sig": "utf-8-sig",
    "utf-8sig": "utf-8-sig",
    "gb18030": "gb18030",
    "gbk": "gb18030",
    "gb2312": "gb18030",
    "cp936": "gb18030",
}


def canonical_encoding(value: str | None) -> str | None:
    if value is None:
        return None
    return _ALIASES.get(str(value).strip().lower().replace(" ", ""))


def score(text: str) -> int:
    """与既有判定规则保持一致：汉字加分，控制字符重罚。"""
    return (
        sum(1 for c in text if "一" <= c <= "鿿") * 4
        - sum(1 for c in text if ord(c) < 32 and c not in "\r\n\t") * 8
    )


def candidates(blob: bytes) -> list[dict[str, Any]]:
    """列出所有严格可解码的候选（不抛异常）。"""
    # BOM 是明确的编码签名，出现时不再让其他编码参与试读。
    names = ("utf-8-sig",) if blob.startswith(BOM) else ENCODINGS
    result: list[dict[str, Any]] = []
    for name in names:
        try:
            text = blob.decode(name, "strict")
        except UnicodeDecodeError:
            continue
        if "�" in text:  # 严格解码理论上不会出现，保留为防御
            continue
        result.append({"encoding": name, "text": text, "score": score(text)})
    return result


def archive_key(header: list[str] | None) -> str | None:
    """档号列。档号值始终逐字保留，不做数值化。"""
    if not header:
        return None
    for key in header:
        if "档号" in str(key):
            return key
    for key in header:
        if str(key).strip().lower() in {"archive_id", "archive_no", "archiveid", "archive_no."}:
            return key
    return header[0]


def preferred(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cands:
        return None
    # 与 M2 既有规则一致的稳定裁决：分数优先，同分优先 UTF-8。
    return max(cands, key=lambda item: (item["score"], item["encoding"] == "utf-8-sig"))


def describe(candidate: dict[str, Any]) -> dict[str, Any]:
    """把一个解码候选解析成复核员可看的试读形状（不含正式档号）。"""
    from app.modules.m1_intake.core import scan  # 延迟导入，避免循环依赖

    parsed = scan(candidate["text"])
    header = list(parsed["header"] or [])
    key = archive_key(header)
    trial_ids: list[str] = []
    if key is not None:
        trial_ids = [str(row[key]) for row in parsed["rows"] if key in row]
    return {
        "encoding": candidate["encoding"],
        "score": candidate["score"],
        "columns": len(header),
        "row_count": len(parsed["rows"]),
        "quarantine_count": len(parsed["quarantine"]),
        "empty_count": parsed["empty"],
        "header": header,
        "archive_field": key,
        "text": candidate["text"],
        # 刻意不叫 archive_ids：试读档号不是正式编号，下游只能看不能采。
        "trial_archive_ids": trial_ids,
        "preview_archive_ids": list(trial_ids),
        "complete": not parsed["quarantine"],
        "replacement_free": "�" not in candidate["text"],
    }


def inspect(blob: bytes) -> dict[str, Any]:
    """完整试读结果，任何字段都不构成正式采信。"""
    described = [describe(item) for item in candidates(blob)]
    best = preferred(described)

    ambiguous = False
    if len(described) >= 2:
        complete = [
            item for item in described
            if item["complete"] and item["replacement_free"] and item["header"]
        ]
        columns = {item["columns"] for item in complete}
        id_sets = {tuple(item["trial_archive_ids"]) for item in complete}
        # 两种编码都能完整读完、列数一致、档号却不同 —— 必须由复核员核定。
        ambiguous = (
            len(complete) == len(described)
            and len(columns) == 1
            and len(id_sets) > 1
        )

    if not described:
        reason = "DECODE_FAILED"
    elif ambiguous:
        reason = "ENCODING_AMBIGUOUS"
    else:
        reason = "OK"

    out: dict[str, Any] = {
        "status": "READY",
        "stage": "TRIAL",
        "formal": False,
        "trial_status": "TRIAL",
        "source_digest": sha256(blob).hexdigest(),
        "requires_confirmation": bool(described),
        "requires_decision": ambiguous,
        "ambiguous": ambiguous,
        "reason": reason,
        "candidates": described,
        # 试读不交付正式编号：下游拿不到正式字段。
        "formal_archive_ids": None,
        "archive_ids": None,
        "input_count": len(blob),
    }
    if best is not None:
        out.update(
            {
                "encoding": best["encoding"],
                "text": best["text"],
                "score": best["score"],
                "header": best["header"],
                "columns": best["columns"],
                "trial_archive_ids": best["trial_archive_ids"],
                "preview_archive_ids": best["trial_archive_ids"],
                "row_count": best["row_count"],
                "empty_count": best["empty_count"],
                "quarantine_count": best["quarantine_count"],
                "valid_count": best["row_count"],
            }
        )
    else:
        out.update(
            {
                "encoding": "REJECTED",
                "text": "",
                "score": 0,
                "header": [],
                "columns": 0,
                "trial_archive_ids": [],
                "preview_archive_ids": [],
                "row_count": 0,
                "empty_count": 0,
                "quarantine_count": 1,
                "valid_count": 0,
            }
        )
    return out
