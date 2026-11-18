from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M3-字段保形"])


def _store(request: Request):
    return request.app.state.adoptions


@router.get("/fields")
def page(request: Request):
    data = execute(store=_store(request))
    return request.app.state.templates.TemplateResponse(request, "m3_fields.html", {"title": "字段保形", "data": data})


@router.post("/api/fields/normalize")
def api(request: Request, payload: dict = Body(default={})):
    return execute(payload or None, store=_store(request))
