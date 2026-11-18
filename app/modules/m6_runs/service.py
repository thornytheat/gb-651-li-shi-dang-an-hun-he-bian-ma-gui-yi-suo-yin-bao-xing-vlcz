from . import core


def execute(payload: dict | None = None, store=None, adoption_store=None) -> dict:
    payload = payload if payload is not None else core.SAMPLE
    if isinstance(payload, dict) and store is not None and "store" not in payload:
        payload = payload | {"store": store}
    return core.run(payload, adoption_store=adoption_store)
