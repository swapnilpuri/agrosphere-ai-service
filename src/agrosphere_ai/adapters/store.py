"""AI-only PostgreSQL journal/immutable registry. No source-service SQL."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from agrosphere_ai.domain.baselines import Conflict, Dataset, Scope, canonical, evaluate
from agrosphere_ai.adapters.codec import parse_event, revision_from_record, validate_pins, wire
from agrosphere_ai.adapters.reference_http import NotFound, Unavailable


class StaleDataset(Exception):
    pass


class Store:
    def __init__(self, guard):
        self.guard = guard
        self.contracts = tuple(sorted((k, v) for k, v in validate_pins()["files"].items() if k.endswith(".schema.json")))

    @contextmanager
    def connection(self):
        with psycopg.connect(self.guard.dsn, connect_timeout=5) as conn:
            self.guard.database(conn)
            conn.execute("SET LOCAL statement_timeout='5s'")
            yield conn

    def ingest(self, row):
        outcome = self._ingest(row)
        # Returning from _ingest exits its context and successfully commits the source fact.
        # A crash/unknown commit outcome leaves no availability marker and no input ACK.
        self.confirm_available(row.event_id, row.body_hash)
        return outcome

    def confirm_available(self, event_id, body_hash):
        with self.connection() as conn:
            row = conn.execute("SELECT first_received_at,clock_timestamp() FROM ai_source_revision WHERE event_id=%s AND body_hash=%s", (event_id, body_hash)).fetchone()
            if row is None or row[1] < row[0]:
                raise Unavailable("Receipt confirmation or clock unavailable")
            conn.execute("INSERT INTO ai_availability VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (event_id, body_hash, row[1]))

    def _ingest(self, row):
        self.guard.verify(row.scope)
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (row.event_id,))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,3))", (row.collection_id,))
            prior = conn.execute("SELECT body_hash,estate_id::text,block_id::text FROM ai_source_revision WHERE event_id=%s", (row.event_id,)).fetchall()
            if any(p[0] == row.body_hash for p in prior):
                # Preserve diagnosis on redelivery after a lost mandatory confirm.
                conflicted = conn.execute("SELECT 1 FROM ai_conflict WHERE event_id=%s LIMIT 1", (row.event_id,)).fetchone()
                return "CONFLICT" if conflicted else "DUPLICATE"
            roots = conn.execute("SELECT event_id::text,estate_id::text,block_id::text,body->>'version',body->>'status',body->>'estate_timezone' FROM ai_source_revision WHERE body->>'collection_id'=%s", (row.collection_id,)).fetchall()
            conflict = bool(prior) or any((p[1], p[2]) != (row.scope.estate_id, row.scope.block_id) or int(p[3]) == row.version or p[5] != row.estate_timezone or p[4] == 'VOIDED' and int(p[3]) < row.version and row.status != 'VOIDED' for p in roots)
            scopes = sorted({(p[1], p[2]) for p in prior} | {(p[1], p[2]) for p in roots} | {(row.scope.estate_id, row.scope.block_id)})
            for estate, block in scopes:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))", (estate + ":" + block,))
            count = conn.execute("SELECT count(*) FROM ai_source_revision WHERE estate_id=%s AND block_id=%s", (row.scope.estate_id, row.scope.block_id)).fetchone()[0]
            if count >= 1000:
                raise ValueError("ADMISSION_LIMIT")
            # Database-assigned first receipt, not an event or caller supplied historical timestamp.
            received = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            row = replace(row, available_at=received, first_received_at=received)
            conn.execute("INSERT INTO ai_source_revision(event_id,body_hash,estate_id,block_id,first_received_at,body) VALUES (%s,%s,%s,%s,%s,%s)",
                         (row.event_id, row.body_hash, row.scope.estate_id, row.scope.block_id, received, Jsonb(json.loads(canonical(row)))))
            if conflict:
                for estate, block in scopes:
                    conn.execute("INSERT INTO ai_conflict(estate_id,block_id,event_id,body_hash,source_at,observed_at) VALUES (%s,%s,%s,%s,%s,%s)", (estate, block, row.event_id, row.body_hash, row.source_at, received))
                return "CONFLICT"
            return "COMMITTED"

    def live_dataset(self, scope):
        self.guard.verify(scope)
        with self.connection() as conn:
            rows = conn.execute("SELECT r.body,a.confirmed_at FROM ai_source_revision r LEFT JOIN ai_availability a USING(event_id,body_hash) WHERE r.estate_id=%s AND r.block_id=%s ORDER BY r.event_id,r.body_hash LIMIT 1001", (scope.estate_id, scope.block_id)).fetchall()
            conflicts = conn.execute("SELECT c.event_id::text,c.source_at,a.confirmed_at FROM ai_conflict c LEFT JOIN ai_availability a USING(event_id,body_hash) WHERE c.estate_id=%s AND c.block_id=%s", (scope.estate_id, scope.block_id)).fetchall()
        if any(r[1] is None for r in rows) or any(c[2] is None for c in conflicts):
            raise Unavailable("Source commit awaits confirmation")
        zone = next(s["timezone"] for s in self.guard.value["scopes"] if s["estateId"] == scope.estate_id and s["blockId"] == scope.block_id)
        return Dataset(scope, zone, "LIVE_FIRST_RECEIPT", tuple(replace(revision_from_record(r[0]), available_at=r[1]) for r in rows), self.contracts,
                       "LIVE_JOURNAL_V1", self.guard.value["runId"], "OBSERVED_AFTER_SOURCE_COMMIT_V1", tuple(Conflict(*r) for r in conflicts))

    def dataset(self, scope, dataset_id, mode):
        self.guard.verify(scope)
        if mode == "LIVE_FIRST_RECEIPT":
            ds = self.live_dataset(scope)
            if ds.dataset_id != dataset_id:
                raise StaleDataset("Live dataset changed")
            return ds
        if mode != "GENERATED_POINT_IN_TIME":
            raise ValueError("Unknown chronology mode")
        with self.connection() as conn:
            row = conn.execute("SELECT body FROM ai_dataset WHERE dataset_id=%s AND estate_id=%s AND block_id=%s", (dataset_id, scope.estate_id, scope.block_id)).fetchone()
        if row is None:
            raise NotFound("Unknown scoped dataset")
        body = row[0]
        ds = Dataset(scope, body["estate_timezone"], body["mode"], tuple(revision_from_record(r) for r in body["revisions"]),
                     tuple(tuple(p) for p in body["source_contracts"]), body["generator"], body["seed"], body["clock_policy"])
        if ds.dataset_id != dataset_id:
            raise ValueError("Immutable dataset fingerprint mismatch")
        return ds

    def record(self, kind, scope, dataset_id, record_id):
        self.guard.verify(scope)
        if kind not in {"model", "evaluation"}:
            raise ValueError("Unsupported record")
        with self.connection() as conn:
            row = conn.execute("SELECT body FROM ai_" + kind + " WHERE " + kind + "_id=%s AND dataset_id=%s AND estate_id=%s AND block_id=%s", (record_id, dataset_id, scope.estate_id, scope.block_id)).fetchone()
        if row is None:
            raise NotFound("Unknown scoped record")
        return row[0]

    def import_generated(self, corpus_path):
        """Offline owned fixture command only; never a business HTTP endpoint."""
        self.guard.verify()
        path = Path(corpus_path)
        if path.stat().st_size > 64 * 1048576:
            raise ValueError("Corpus admission limit")
        raw = path.read_bytes()
        corpus_hash = hashlib.sha256(raw).hexdigest()
        if corpus_hash != self.guard.value["corpusSha256"]:
            raise PermissionError("Unreviewed corpus")
        body = json.loads(raw)
        scope = Scope(**body["scope"])
        self.guard.verify(scope)
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,2))", (corpus_hash,))
            prior = conn.execute("SELECT dataset_id FROM ai_dataset WHERE corpus_hash=%s AND estate_id=%s AND block_id=%s", (corpus_hash, scope.estate_id, scope.block_id)).fetchone()
            if prior:
                return prior[0]
            if len(body["events"]) > 1000:
                raise ValueError("Corpus revision limit")
            received = conn.execute("SELECT clock_timestamp()").fetchone()[0]
            rows = tuple(replace(parse_event(json.dumps(entry["event"]).encode(), received),
                                 available_at=datetime.fromisoformat(entry["availableAt"])) for entry in body["events"])
            ds = Dataset(scope, body["timezone"], "GENERATED_POINT_IN_TIME", rows, self.contracts, body["generator"], body["seed"], "FIXTURE_ASSIGNED_AVAILABILITY_NOT_HISTORICAL_RECEIPT_V1")
            ev = evaluate(ds, date.fromisoformat(body["startDate"]), tuple((t, date.fromisoformat(d)) for t, d in body.get("syntheticAnomalies", [])))
            conn.execute("INSERT INTO ai_dataset VALUES (%s,%s,%s,%s,%s)", (ds.dataset_id, corpus_hash, scope.estate_id, scope.block_id, Jsonb(json.loads(canonical(ds)))))
            for model in ev.models:
                conn.execute("INSERT INTO ai_model VALUES (%s,%s,%s,%s,%s)", (model.model_id, ds.dataset_id, scope.estate_id, scope.block_id, Jsonb(wire(model))))
            summary = wire(ev)
            del summary["forecasts"]
            summary["evaluationId"] = ev.evaluation_id
            conn.execute("INSERT INTO ai_evaluation VALUES (%s,%s,%s,%s,%s)", (ev.evaluation_id, ds.dataset_id, scope.estate_id, scope.block_id, Jsonb(summary)))
            for event_id, body_hash in sorted({(r.event_id, r.body_hash) for r in rows}):
                conn.execute("INSERT INTO ai_evaluation_source VALUES (%s,%s,%s)", (ev.evaluation_id, event_id, body_hash))
            return ds.dataset_id
