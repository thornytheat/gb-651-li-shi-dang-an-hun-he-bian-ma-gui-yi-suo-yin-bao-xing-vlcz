from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M1-文件采信"])


def _store(request: Request):
    return request.app.state.adoptions


@router.get("/intake")
def page(request: Request):
    data = execute(store=_store(request))
    return request.app.state.templates.TemplateResponse(request, "m1_intake.html", {"title": "文件采信", "data": data})


@router.post("/api/intake/run")
def api(request: Request, payload: dict = Body(default={})):
    return execute(payload, store=_store(request))


@router.post("/api/intake/trial")
def trial_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "trial"} | (payload or {}), store=_store(request))


@router.post("/api/intake/confirm")
def confirm_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "confirm"} | (payload or {}), store=_store(request))


@router.post("/api/intake/adopt")
def adopt_api(request: Request, payload: dict = Body(default={})):
    return execute({"action": "adopt"} | (payload or {}), store=_store(request))
