"""Explicit synthetic replay clock; source shapes copied from pinned public examples."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5
import json
from agrosphere_ai.adapters.codec import CONTRACTS
from agrosphere_ai.domain.baselines import midnight


def generated_corpus(scope, zone, start, seed):
    corpus = {"scope": {"estate_id": scope.estate_id, "block_id": scope.block_id}, "timezone": zone,
              "startDate": start.isoformat(), "generator": "M4_LINEAR_WITH_FINAL_SPIKE_V1", "seed": seed,
              "syntheticAnomalies": [[target, (start + timedelta(days=62)).isoformat()] for target in ("WET", "DRY")], "events": []}
    templates = [json.loads(CONTRACTS.joinpath(name + ".example.json").read_text()) for name in ("LatexCollectionRecorded", "DrcMeasurementRecorded")]
    identity = lambda label: str(uuid5(NAMESPACE_URL, f"m4:{scope.estate_id}:{scope.block_id}:{seed}:{label}"))
    stamp = lambda instant: instant.isoformat().replace("+00:00", "Z")
    for i in range(63):
        day = start + timedelta(days=i)
        base = midnight(day, zone)
        wet = Decimal(10 + i + (10 if i == 62 else 0)).quantize(Decimal("0.001"))
        exact = wet * Decimal("20.0") / 100
        for version, template in enumerate(templates):
            event = deepcopy(template)
            source_at = base + timedelta(hours=11 + 2 * version)
            source_time = stamp(source_at)
            event.update(eventId=identity(f"event:{i}:{version}"), estateId=scope.estate_id, blockId=scope.block_id,
                         aggregateId=identity(f"collection:{i}"), aggregateVersion=version, timestamp=source_time,
                         correlationId=identity(f"correlation:{i}"))
            c = event["payload"]["collection"]
            c.update(id=event["aggregateId"], estateId=scope.estate_id, blockId=scope.block_id, version=version,
                     estateTimezone=zone, collectionDate=day.isoformat(), tappingDate=day.isoformat(),
                     collectedAt=stamp(base + timedelta(hours=10)), masterValidatedAt=stamp(base + timedelta(hours=9)),
                     createdAt=stamp(base + timedelta(hours=11)), updatedAt=source_time, wetLatexKg=float(wet),
                     tappingRecordId=identity(f"tapping:{i}"), assignmentId=identity(f"assignment:{i}"),
                     collectionReference=f"SIMULATED-M4-{i}")
            if version:
                measurement = c["currentMeasurement"]
                measurement.update(id=identity(f"measurement:{i}"), collectionId=c["id"], drcPercent=20.0,
                                   measuredAt=stamp(base + timedelta(hours=12)), recordedAt=source_time)
                c.update(dryRubberExactKg=float(exact), dryRubberKg=float(exact.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)))
                event["payload"]["measurementId"] = measurement["id"]
            corpus["events"].append({"event": event, "availableAt": stamp(source_at + timedelta(seconds=1))})
    return corpus
