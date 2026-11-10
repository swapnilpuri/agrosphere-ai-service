"""Independent pinned-contract codec; public bytes only, never source-repository reads."""
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
import hashlib
import json
import re
from uuid import UUID
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator, FormatChecker
from agrosphere_ai.domain.baselines import Revision, Scope, canonical, fingerprint

NAMES = {"LatexCollectionRecorded": "RECORDED", "DrcMeasurementRecorded": "DRC_RECORDED",
         "LatexCollectionAdjusted": "ADJUSTED", "LatexCollectionVoided": "VOIDED"}
CONTRACTS = files("agrosphere_ai").joinpath("contracts")


def validate_pins():
    meta = json.loads(CONTRACTS.joinpath("RELEASE.json").read_text())
    if meta["release"] != "0.13.0":
        raise ValueError("Unsupported AI release")
    for path, expected in meta["files"].items():
        if hashlib.sha256(CONTRACTS.joinpath(path).read_bytes()).hexdigest() != expected:
            raise ValueError("AI pin mismatch")
    return meta


def camel(key):
    return re.sub(r"_([a-z])", lambda m: m[1].upper(), key)


def wire(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {camel(k): wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def load_json(raw):
    if len(raw) > 1048576:
        raise ValueError("ADMISSION_LIMIT")
    def decimal_value(token):
        if len(token) > 50 or len(re.sub(r"[^0-9]", "", token.split("e")[0].split("E")[0])) > 32:
            raise ValueError("ADMISSION_LIMIT")
        exponent = re.split(r"[eE]", token)
        if len(exponent) > 1 and abs(int(exponent[1])) > 12:
            raise ValueError("ADMISSION_LIMIT")
        return Decimal(token)
    def integer_value(token):
        if len(token.lstrip("-")) > 19:
            raise ValueError("ADMISSION_LIMIT")
        return int(token)
    return json.loads(raw, parse_float=decimal_value, parse_int=integer_value, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def parse_event(raw, received_at):
    event = load_json(raw)
    name = event.get("eventType")
    if name not in NAMES:
        raise ValueError("Unsupported event")
    schema = json.loads(CONTRACTS.joinpath(name + ".schema.json").read_text(), parse_float=Decimal)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(event)
    if event["dataProvenance"] != "SIMULATED":
        raise PermissionError("Outside generated source authority")
    c = event["payload"]["collection"]
    for outer, inner in (("aggregateId", "id"), ("aggregateVersion", "version"), ("estateId", "estateId"),
                         ("blockId", "blockId"), ("dataProvenance", "dataProvenance")):
        if event[outer] != c[inner]:
            raise ValueError("Envelope/snapshot mismatch")
    stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    if stamp != datetime.fromisoformat(c["updatedAt"].replace("Z", "+00:00")):
        raise ValueError("Source chronology mismatch")
    at = lambda key: datetime.fromisoformat(c[key].replace("Z", "+00:00"))
    if not at("masterValidatedAt") <= at("createdAt") <= at("updatedAt") or at("collectedAt") > stamp:
        raise ValueError("Invalid audit chronology")
    if at("collectedAt").astimezone(ZoneInfo(c["estateTimezone"])).date().isoformat() != c["collectionDate"] or c["collectionDate"] < c["tappingDate"]:
        raise ValueError("Invalid estate-local collection date")
    wet = Decimal(c["wetLatexKg"])
    exact = None if c["dryRubberExactKg"] is None else Decimal(c["dryRubberExactKg"])
    dry = None if c["dryRubberKg"] is None else Decimal(c["dryRubberKg"])
    if exact is not None:
        measurement = c["currentMeasurement"]
        if measurement is None or wet * Decimal(measurement["drcPercent"]) / 100 != exact:
            raise ValueError("Source DRC arithmetic mismatch")
        measured = datetime.fromisoformat(measurement["measuredAt"].replace("Z", "+00:00"))
        recorded = datetime.fromisoformat(measurement["recordedAt"].replace("Z", "+00:00"))
        if measurement["collectionId"] != c["id"] or measurement["dataProvenance"] != c["dataProvenance"] or measurement["introducedVersion"] > c["version"] or not at("collectedAt") <= measured <= recorded <= stamp:
            raise ValueError("Invalid measurement evidence")
    payload = event["payload"]
    if name == "LatexCollectionRecorded":
        if c["createdAt"] != c["updatedAt"] or payload["reason"] != c["backdateReason"]:
            raise ValueError("Invalid creation evidence")
    elif name == "DrcMeasurementRecorded":
        if payload["measurementId"] != c["currentMeasurement"]["id"] or c["currentMeasurement"]["introducedVersion"] != c["version"]:
            raise ValueError("Invalid measurement revision")
    else:
        adjustment = payload["adjustment"]
        if not payload["previousVersion"] == c["version"] - 1 == adjustment["beforeVersion"] or adjustment["afterVersion"] != c["version"] or adjustment["approvedBy"] != c["updatedBy"] or adjustment["approvedAt"] != c["updatedAt"] or adjustment["correlationId"] != event["correlationId"]:
            raise ValueError("Invalid adjustment evidence")
    # Fingerprint the validated full envelope in RAM; retain no actors/reasons/raw payload.
    return Revision(str(UUID(event["eventId"])), fingerprint(event), str(UUID(c["id"])), c["version"],
                    Scope(str(UUID(c["estateId"])), str(UUID(c["blockId"]))), date.fromisoformat(c["collectionDate"]),
                    c["estateTimezone"], NAMES[name], c["status"], wet.quantize(Decimal("0.001")),
                    None if dry is None else dry.quantize(Decimal("0.001")), exact, c["calculationPolicy"],
                    stamp, received_at, received_at, event["dataProvenance"])


def revision_from_record(record):
    value = dict(record)
    value["scope"] = Scope(**value["scope"])
    value["collection_date"] = date.fromisoformat(value["collection_date"])
    for key in ("source_at", "available_at", "first_received_at"):
        value[key] = datetime.fromisoformat(value[key])
    for key in ("wet_kg", "dry_kg", "dry_exact_kg"):
        if value[key] is not None:
            value[key] = Decimal(value[key])
    return Revision(**value)


def validate_response(name, value):
    doc = json.loads(CONTRACTS.joinpath("ai-service.openapi.json").read_text())
    Draft202012Validator({**doc, "$ref": "#/components/schemas/" + name}, format_checker=FormatChecker()).validate(value)
    return value
