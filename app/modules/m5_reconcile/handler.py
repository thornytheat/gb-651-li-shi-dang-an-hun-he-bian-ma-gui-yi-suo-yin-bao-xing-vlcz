from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M5-索引对账"])


def _store(request: Request):
    return request.app.state.adoptions


@router.get("/reconcile")
def page(request: Request):
    data = execute(store=_store(request))
    return request.app.state.templates.TemplateResponse(request, "m5_reconcile.html", {"title": "索引对账", "data": data})


@router.post("/api/reconcile")
def api(request: Request, payload: dict = Body(default={})):
    return execute(payload or None, store=_store(request))
