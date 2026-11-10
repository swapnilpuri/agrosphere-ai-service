from copy import deepcopy
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import json
import pytest
from agrosphere_ai.adapters.codec import canonical, parse_event, validate_pins, wire, validate_response
from agrosphere_ai.adapters.generated_corpus import generated_corpus
from agrosphere_ai.domain.baselines import Dataset, evaluate, forecast, midnight
from test_baselines import START, SCOPE


def corpus():
    return generated_corpus(SCOPE, "UTC", START, "test")


def parse(entry):
    return parse_event(json.dumps(entry["event"]).encode(), datetime.now(timezone.utc))


def test_all_generated_source_events_validate_and_retain_no_private_fields():
    value = corpus()
    rows = [parse(entry) for entry in value["events"]]
    assert len(rows) == 126
    retained = canonical(rows)
    for forbidden in ["workerId", "createdBy", "updatedBy", "SIMULATED-SUPERVISOR", "reason", "sourceReference"]:
        assert forbidden not in retained
    assert len(validate_pins()["files"]) == 7


def test_schema_cross_field_arithmetic_and_duplicate_json_fail_closed():
    value = corpus()["events"][1]
    for mutate in [lambda e: e.update(aggregateVersion=0), lambda e: e.update(estateId="00000000-0000-0000-0000-000000000099"),
                   lambda e: e["payload"]["collection"].update(dryRubberExactKg=0),
                   lambda e: e["payload"]["collection"].update(unexpected=True)]:
        bad = deepcopy(value)
        mutate(bad["event"])
        with pytest.raises(Exception):
            parse(bad)
    with pytest.raises(ValueError):
        parse_event(b'{"eventType":"LatexCollectionRecorded","eventType":"DrcMeasurementRecorded"}', datetime.now(timezone.utc))
    with pytest.raises(ValueError):
        parse_event(b" " * 1048577, datetime.now(timezone.utc))
    for token in (b'0e2147483647', b'1e-100000000', b'1234567890123456789012345'):
        with pytest.raises(ValueError):
            parse_event(b'{"value":' + token + b'}', datetime.now(timezone.utc))


def test_generated_protocol_end_to_end_source_decode_and_contract_responses():
    c = corpus()
    rows = tuple(replace(parse(entry), available_at=datetime.fromisoformat(entry["availableAt"].replace("Z", "+00:00"))) for entry in c["events"])
    contracts = tuple(sorted((k, v) for k, v in validate_pins()["files"].items() if k.endswith(".schema.json")))
    ds = Dataset(SCOPE, "UTC", "GENERATED_POINT_IN_TIME", rows, contracts, c["generator"], c["seed"], "GENERATED_AVAILABILITY_V1")
    ev = evaluate(ds, START, tuple((t, datetime.fromisoformat(d).date()) for t, d in c["syntheticAnomalies"]))
    f = forecast(ds, midnight(START + timedelta(days=62), "UTC"), midnight(START + timedelta(days=63), "UTC"))
    assert f.targets[0].predicted_kg == __import__('decimal').Decimal("68.000")
    assert f.targets[0].actual_kg == __import__('decimal').Decimal("82.000")
    assert all(t.anomaly_status == "FLAGGED" for t in f.targets)
    validate_response("Forecast", wire(f))
    for model in ev.models:
        validate_response("ModelRecord", wire(model))
    summary = wire(ev)
    del summary["forecasts"]
    summary["evaluationId"] = ev.evaluation_id
    validate_response("Evaluation", summary)
