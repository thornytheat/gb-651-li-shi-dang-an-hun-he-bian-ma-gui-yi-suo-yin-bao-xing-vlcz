from . import core


def execute(payload: dict | None = None, store=None) -> dict:
    return core.run(payload if payload is not None else core.SAMPLE, store=store)
