from . import core


def execute(payload: dict | None = None, store=None) -> dict:
    return core.run(payload, store=store)
