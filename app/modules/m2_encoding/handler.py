from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M2-编码判定"])


def _store(request: Request):
    return request.app.state.adoptions


@router.get("/encoding")
def page(request: Request):
    data = execute(store=_store(request))
    return request.app.state.templates.TemplateResponse(request, "m2_encoding.html", {"title": "编码判定", "data": data})


@router.post("/api/encoding/detect")
def api(request: Request, payload: dict = Body(default={})):
    return execute(payload or None, store=_store(request))


@router.post("/api/encoding/trial")
def trial_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "trial"} | (payload or {}), store=_store(request))


@router.post("/api/encoding/confirm")
def confirm_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "confirm"} | (payload or {}), store=_store(request))


@router.post("/api/encoding/adopt")
def adopt_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "adopt"} | (payload or {}), store=_store(request))
