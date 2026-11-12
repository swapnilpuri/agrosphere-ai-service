from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from agrosphere_ai.adapters.ownership import Guard
from agrosphere_ai.adapters.consumer import Consumer
from test_baselines import SCOPE


def test_guard_requires_exact_manifest_marker_scope_dsn_and_lifetime(tmp_path):
    run = str(uuid4())
    dsn = "postgresql://generated-runtime:generated-password@postgres:5432/generated"
    now = datetime.now(timezone.utc)
    manifest = dict(runId=run, project="agrosphere-3a-check-1234abcd", createdAt=now.isoformat(), expiresAt=(now + timedelta(hours=1)).isoformat(),
                    dsnSha256=hashlib.sha256(dsn.encode()).hexdigest(), dbMarker="M4:" + run,
                    scopes=[dict(estateId=SCOPE.estate_id, blockId=SCOPE.block_id)])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    marker = tmp_path / ".generated-m4-owner"
    marker.write_text(run)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    guard = Guard(path, digest, dsn)
    guard.verify(SCOPE)
    with pytest.raises(PermissionError):
        Guard(path, digest, dsn + "wrong")
    path.write_text(json.dumps({**manifest, "expiresAt": (now + timedelta(hours=5)).isoformat()}))
    with pytest.raises(PermissionError):
        guard.verify()
    with pytest.raises(PermissionError):
        Guard(path, hashlib.sha256(path.read_bytes()).hexdigest(), dsn)
    path.write_text(json.dumps(manifest))
    marker.write_text("wrong")
    with pytest.raises(PermissionError):
        guard.verify()


def test_poc_close_project_is_bound_to_run_and_two_hour_window(tmp_path):
    run = str(uuid4())
    dsn = "postgresql://generated-runtime:generated-password@postgres:5432/generated"
    now = datetime.now(timezone.utc)
    manifest = dict(runId=run, project="agrosphere-poc-close-" + run.replace("-", ""),
                    createdAt=now.isoformat(), expiresAt=(now + timedelta(hours=2)).isoformat(),
                    dsnSha256=hashlib.sha256(dsn.encode()).hexdigest(), dbMarker="M4:" + run,
                    scopes=[dict(estateId=SCOPE.estate_id, blockId=SCOPE.block_id)])
    path = tmp_path / "manifest.json"
    (tmp_path / ".generated-m4-owner").write_text(run)
    def guard(value):
        path.write_text(json.dumps(value))
        return Guard(path, hashlib.sha256(path.read_bytes()).hexdigest(), dsn)
    guard(manifest).verify(SCOPE)
    for project in ["agrosphere-poc-close-" + uuid4().hex,
                    manifest["project"] + "-extra", "agrosphere-poc-close-1234abcd", "existing-stack"]:
        with pytest.raises(PermissionError):
            guard({**manifest, "project": project})
    with pytest.raises(PermissionError):
        guard({**manifest, "expiresAt": (now + timedelta(hours=2, seconds=1)).isoformat()})


@pytest.mark.parametrize("outcome,diagnostic_error,expected", [("COMMITTED", False, ["commit", "ack"]),
    ("DUPLICATE", False, ["commit", "ack"]), ("CONFLICT", False, ["commit", "confirm", "ack"]),
    ("CONFLICT", True, ["commit", "confirm"]), ("INVALID", False, ["confirm", "ack"]),
    ("STORAGE_FAILURE", False, ["commit"])])
def test_commit_and_mandatory_confirm_precede_ack_or_pause(monkeypatch, outcome, diagnostic_error, expected):
    calls = []
    class Channel:
        def basic_qos(self, **kwargs): pass
        def confirm_delivery(self): pass
        def consume(self, *args, **kwargs):
            yield SimpleNamespace(delivery_tag=1), None, b"SIMULATED-INPUT-NOT-LOGGED"
        def basic_publish(self, **kwargs):
            calls.append("confirm")
            assert kwargs["mandatory"] is True
            assert set(json.loads(kwargs["body"])) == {"policy", "reason"}
            if diagnostic_error:
                raise RuntimeError("mandatory return")
        def basic_ack(self, tag): calls.append("ack")
    channel = Channel()
    connection = SimpleNamespace(is_open=True, channel=lambda: channel, close=lambda: None)
    monkeypatch.setattr("agrosphere_ai.adapters.consumer.pika.BlockingConnection", lambda *args: connection)
    def parse(*args):
        if outcome == "INVALID": raise ValueError("bad body")
        return object()
    monkeypatch.setattr("agrosphere_ai.adapters.consumer.parse_event", parse)
    def ingest(row):
        calls.append("commit")
        if outcome == "STORAGE_FAILURE": raise RuntimeError("storage unavailable")
        return outcome
    store = SimpleNamespace(guard=SimpleNamespace(verify=lambda: None), ingest=ingest)
    consumer = Consumer(store, "amqp://generated:generated@localhost/%2F", "owned.queue", "owned.diagnostics")
    consumer.run()
    assert calls == expected
    assert consumer.paused == (diagnostic_error or outcome == "STORAGE_FAILURE")
