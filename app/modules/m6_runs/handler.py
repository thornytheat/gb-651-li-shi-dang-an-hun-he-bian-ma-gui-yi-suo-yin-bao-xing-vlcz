from fastapi import APIRouter, Request
from fastapi.responses import Response, JSONResponse
from .service import execute
from ...errors import DomainError

router = APIRouter(tags=["M6-批次导出"])


def _store(request: Request):
    return request.app.state.store


def _adoptions(request: Request):
    return request.app.state.adoptions


_PENDING = {
    "status": "STALE",
    "stage": "RECOMPUTING",
    "formal": False,
    "reason": "STALE_VERSION",
    "input_count": 0,
    "valid_count": 0,
    "quarantine_count": 0,
}


@router.get("/runs")
def page(request: Request):
    try:
        data = execute({"store": _store(request)}, adoption_store=_adoptions(request))
    except DomainError as exc:
        data = _PENDING | {"message": exc.message}
    return request.app.state.templates.TemplateResponse(request, "m6_runs.html", {"title": "批次导出", "data": data})


@router.get("/api/runs")
def runs(request: Request):
    try:
        execute({"store": _store(request)}, adoption_store=_adoptions(request))
    except DomainError:
        # 重算未完成时不生成新的批次摘要，只回显已有运行。
        pass
    return {"runs": _store(request).all()}


@router.get("/api/export/evidence.csv")
def export(request: Request, adoption_id: str | None = None, source_digest: str | None = None):
    body = {}
    if adoption_id:
        body["adoption_id"] = adoption_id
    if source_digest:
        body["source_digest"] = source_digest
    body["store"] = _store(request)
    try:
        data = execute(body, adoption_store=_adoptions(request))
    except DomainError as exc:
        # 改判后旧采信已过期 / 重算未完成：宁可报错，也不导出可能混用的字段。
        return JSONResponse(status_code=422, content={"error": exc.code, "message": exc.message})
    return Response(data["csv"], media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=evidence.csv"})
