"""编码核定与采信更新的核心约束。"""
import os

import pytest

from app.adoption import AdoptionStore, adopt, confirm, current_adoption, trial
from app.errors import DomainError
from app.modules.m3_fields.core import run as fields_run
from app.modules.m5_reconcile.core import run as reconcile_run
from app.modules.m6_runs.core import run as export_run

# 两种编码都能完整读完、列数一致（2 列），但档号不同。
AMBIGUOUS = "档甲,盒号\n档乙,07\n".encode("utf-8")
AMBIGUOUS_HEX = AMBIGUOUS.hex()


@pytest.fixture
def store(tmp_path):
    return AdoptionStore(str(tmp_path / "adoption.db"))


def test_trial_never_exposes_formal_ids(store):
    out = trial(AMBIGUOUS, store)
    assert out["formal"] is False
    assert out["stage"] == "TRIAL"
    assert out["archive_ids"] is None
    assert out["formal_archive_ids"] is None
    assert out["ambiguous"] is True
    assert out["reason"] == "ENCODING_AMBIGUOUS"
    assert out["trial_archive_ids"]


def test_trial_ambiguous_candidates_same_columns_different_ids(store):
    out = trial(AMBIGUOUS, store)
    assert len(out["candidates"]) == 2
    columns = {item["columns"] for item in out["candidates"]}
    ids = {tuple(item["trial_archive_ids"]) for item in out["candidates"]}
    assert len(columns) == 1
    assert len(ids) == 2


def test_trial_result_not_accepted_by_downstream(store):
    out = trial(AMBIGUOUS, store)
    with pytest.raises(DomainError) as info:
        fields_run(out, store=store)
    assert info.value.code == "TRIAL_NOT_FORMAL"
    with pytest.raises(DomainError):
        reconcile_run(out, store=store)


def test_confirmation_produces_formal_ids(store):
    out = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    assert out["formal"] is True
    assert out["archive_ids"] == ["档乙"]
    assert out["adoption_id"]
    assert out["generation"] == 1


def test_rejudge_marks_old_adoption_stale_and_recomputes(store):
    first = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    second = confirm(store, blob=AMBIGUOUS, encoding="gb18030")
    assert second["generation"] == 2
    assert second["archive_ids"] != first["archive_ids"]
    assert second["stale_adoption_id"] == first["adoption_id"]
    with pytest.raises(DomainError) as info:
        current_adoption(store, adoption_id=first["adoption_id"])
    assert info.value.code == "STALE_VERSION"


def test_stale_adoption_link_rejected_by_fields(store):
    first = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    confirm(store, blob=AMBIGUOUS, encoding="gb18030")
    with pytest.raises(DomainError) as info:
        fields_run({"adoption_id": first["adoption_id"]}, store=store)
    assert info.value.code == "STALE_VERSION"


def test_retry_same_confirmation_does_not_duplicate(store):
    first = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    again = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    assert again["adoption_id"] == first["adoption_id"]
    assert again["confirmation_id"] == first["confirmation_id"]
    assert again["generation"] == first["generation"]
    assert again["idempotent"] is True
    third = adopt(store, source_digest=first["source_digest"])
    assert third["adoption_id"] == first["adoption_id"]
    assert third["idempotent"] is True


def test_confirmation_for_wrong_encoding_rejected(store):
    confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    with pytest.raises(DomainError) as info:
        adopt(
            store,
            source_digest=trial(AMBIGUOUS)["source_digest"],
            confirmation_id="conf-not-a-real-one",
        )
    assert info.value.code == "STALE_VERSION"


def test_no_mixing_across_adoptions(store):
    other = "档甲,盒号\n000123,07\n".encode("utf-8")
    a = current_adoption(store, source_digest=confirm(store, blob=other, encoding="utf-8")["source_digest"])
    b = current_adoption(store, source_digest=confirm(store, blob=AMBIGUOUS, encoding="utf-8")["source_digest"])
    with pytest.raises(DomainError) as info:
        fields_run({"adoption_id": a["adoption_id"], "rows": a["rows"] + b["rows"]}, store=store)
    assert info.value.code == "MIXED_ADOPTION"
    with pytest.raises(DomainError) as info:
        fields_run(
            {"adoption_id": b["adoption_id"], "rows": [dict(b["rows"][0], adoption_id=a["adoption_id"])]},
            store=store,
        )
    assert info.value.code == "MIXED_ADOPTION"


def test_fields_use_only_same_adoption_values(store):
    adoption = current_adoption(
        store, source_digest=confirm(store, blob=AMBIGUOUS, encoding="utf-8")["source_digest"]
    )
    tampered = [dict(adoption["rows"][0], fields={"档甲": "999999", "盒号": "07"})]
    with pytest.raises(DomainError) as info:
        fields_run({"adoption_id": adoption["adoption_id"], "rows": tampered}, store=store)
    assert info.value.code == "MIXED_ADOPTION"
    ok = fields_run({"adoption_id": adoption["adoption_id"], "rows": adoption["rows"]}, store=store)
    assert ok["adoption_id"] == adoption["adoption_id"]


def test_recompute_pending_blocks_downstream(store):
    first = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    pending = confirm(store, blob=AMBIGUOUS, encoding="gb18030", compute=False)
    assert pending["requires_adoption"] is True
    assert pending["requires_recompute"] is True
    with pytest.raises(DomainError) as info:
        fields_run({"source_digest": first["source_digest"]}, store=store)
    assert info.value.code == "STALE_VERSION"
    with pytest.raises(DomainError) as info:
        export_run({}, adoption_store=store)
    assert info.value.code == "STALE_VERSION"


def test_recompute_resumes_after_pending(store):
    first = confirm(store, blob=AMBIGUOUS, encoding="utf-8")
    pending = confirm(store, blob=AMBIGUOUS, encoding="gb18030", compute=False)
    resumed = adopt(store, source_digest=pending["source_digest"])
    assert resumed["adoption_id"] != first["adoption_id"]
    assert resumed["encoding"] == "gb18030"
    with pytest.raises(DomainError):
        current_adoption(store, adoption_id=first["adoption_id"])


def test_export_rejects_stale_or_trial(store):
    adoption = current_adoption(
        store, source_digest=confirm(store, blob=AMBIGUOUS, encoding="utf-8")["source_digest"]
    )
    out = export_run({"adoption_id": adoption["adoption_id"]}, adoption_store=store)
    assert "档乙" in out["csv"]
    assert out["adoption_id"] == adoption["adoption_id"]
    with pytest.raises(DomainError) as info:
        export_run({"rows": [dict(adoption["rows"][0], formal=False)]})
    assert info.value.code == "TRIAL_NOT_FORMAL"


def test_api_roundtrip(client):
    trial_resp = client.post("/api/intake/run", json={"action": "trial", "hex": AMBIGUOUS_HEX})
    assert trial_resp.status_code == 200
    assert trial_resp.json()["archive_ids"] is None

    confirmed = client.post(
        "/api/intake/confirm", json={"hex": AMBIGUOUS_HEX, "encoding": "utf-8"}
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["formal"] is True
    assert body["archive_ids"] == ["档乙"]

    again = client.post(
        "/api/intake/confirm", json={"hex": AMBIGUOUS_HEX, "encoding": "utf-8"}
    )
    assert again.json()["adoption_id"] == body["adoption_id"]

    fields = client.post("/api/fields/normalize", json={"adoption_id": body["adoption_id"]})
    assert fields.status_code == 200
    assert fields.json()["adoption_id"] == body["adoption_id"]

    switch = client.post(
        "/api/intake/confirm", json={"hex": AMBIGUOUS_HEX, "encoding": "gb18030"}
    )
    assert switch.json()["archive_ids"] != body["archive_ids"]
    stale = client.post("/api/fields/normalize", json={"adoption_id": body["adoption_id"]})
    assert stale.status_code == 422
    assert stale.json()["error"] == "STALE_VERSION"


def test_trial_from_m2_has_no_formal_ids(client):
    out = client.post("/api/encoding/detect", json={"hex": AMBIGUOUS_HEX}).json()
    assert out["formal"] is False
    assert out["archive_ids"] is None
    ids = {tuple(item["trial_archive_ids"]) for item in out["candidates"]}
    assert ("档乙",) in ids


def test_confirm_by_digest_only(store):
    probe = trial(AMBIGUOUS, store)
    out = confirm(store, source_digest=probe["source_digest"], encoding="utf-8")
    assert out["archive_ids"] == ["档乙"]
    assert out["idempotent"] is False
