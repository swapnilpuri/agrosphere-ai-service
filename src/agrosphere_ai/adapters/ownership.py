"""Default-disabled activation bound to a short-lived generated fixture, never private env files."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID


class Guard:
    def __init__(self, manifest_path, manifest_hash, dsn):
        self.path = Path(manifest_path)
        self.hash = manifest_hash
        self.dsn = dsn
        self.value = self.verify()

    def verify(self, scope=None):
        raw = self.path.read_bytes()
        if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != self.hash:
            raise PermissionError("Invalid generated manifest")
        m = json.loads(raw)
        run = UUID(m["runId"])
        close_project = "agrosphere-poc-close-" + run.hex
        is_close = m["project"] == close_project and m["runId"] == str(run)
        if not is_close and not re.fullmatch(r"agrosphere-3a-check-[0-9a-f]{8}", m["project"]):
            raise PermissionError("Unowned project")
        if (self.path.parent / ".generated-m4-owner").read_text() != m["runId"]:
            raise PermissionError("Missing ownership marker")
        created = datetime.fromisoformat(m["createdAt"])
        expires = datetime.fromisoformat(m["expiresAt"])
        now = datetime.now(timezone.utc)
        lifetime = timedelta(hours=2 if is_close else 4)
        if created.tzinfo is None or expires.tzinfo is None or not created <= now < expires or expires - created > lifetime:
            raise PermissionError("Generated fixture expired")
        if hashlib.sha256(self.dsn.encode()).hexdigest() != m["dsnSha256"] or m["dbMarker"] != "M4:" + m["runId"]:
            raise PermissionError("Wrong generated database")
        scopes = m["scopes"]
        if not 1 <= len(scopes) <= 10 or len({s["estateId"] for s in scopes}) > 2:
            raise PermissionError("Generated scope limit")
        if scope and not any(s["estateId"] == scope.estate_id and s["blockId"] == scope.block_id for s in scopes):
            raise PermissionError("Scope outside generated allowlist")
        return m

    def database(self, connection):
        self.verify()
        row = connection.execute("SELECT current_database(), current_user, shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
        if row is None or row[2] != self.value["dbMarker"] or row[1] != self.value["runtimeUser"]:
            raise PermissionError("Database ownership mismatch")
        role = connection.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        if role is None or any(role):
            raise PermissionError("Unsafe runtime principal")
