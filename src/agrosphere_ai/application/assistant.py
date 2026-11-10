"""Closed-intent deterministic evidence composition; no free text or external model."""
from datetime import timedelta
import json
from urllib.parse import urlencode
from agrosphere_ai.domain.baselines import fingerprint
from agrosphere_ai.application.ports.m4 import Unavailable

INTENTS = {"PRODUCTION_SUMMARY", "FORECAST_EXPLANATION", "ANOMALY_EXPLANATION", "EVIDENCE_INSPECTION"}


def compose(scope, frozen, intent, reference, bearer, environment_from=None, environment_to=None):
    if intent not in INTENTS:
        raise ValueError("Closed intent required")
    prefix = "/api/v1/ai/estates/" + scope.estate_id + "/blocks/" + scope.block_id
    analytics = "/api/v1/analytics/estates/" + scope.estate_id + "/blocks/" + scope.block_id
    query = {"datasetId": frozen.dataset_id, "mode": frozen.mode, "asOf": frozen.as_of.isoformat()}
    if frozen.label_as_of is not None:
        query["labelAsOf"] = frozen.label_as_of.isoformat()
    forecast_ref = {"kind": "FORECAST", "id": fingerprint(frozen), "href": prefix + "/production/baselines?" + urlencode(query)}
    statements, limitations = [], ["DEMO_ONLY; SIMULATED sources; observed collection coverage only.",
                                   "Uncalibrated thresholds; no agronomic recommendation or causal weather claim."]
    partial = False
    if frozen.mode == "GENERATED_POINT_IN_TIME":
        limitations.append("Historical availability is fixture-assigned, not evidence of historical broker receipt.")
    if intent in {"FORECAST_EXPLANATION", "ANOMALY_EXPLANATION", "EVIDENCE_INSPECTION"}:
        for r in frozen.targets:
            model_ref = {"kind": "MODEL", "id": r.model_id, "href": prefix + "/models/" + r.model_id + "?" + urlencode({"datasetId": frozen.dataset_id})}
            if r.predicted_kg is None:
                text = f"{r.target}: forecast withheld ({r.status}); missing evidence is not zero."
                partial = True
            elif intent == "ANOMALY_EXPLANATION":
                text = (f"{r.target}: {r.anomaly_status}; frozen forecast {r.predicted_kg:.3f} kg. " +
                        (f"Observed actual {r.actual_kg:.3f} kg, residual {r.residual_kg:.3f} kg; flags require absolute residual > {r.threshold_kg:.3f} kg."
                         if r.residual_kg is not None else "No eligible label/residual is available."))
                partial |= r.residual_kg is None
            else:
                text = f"{r.target}: frozen seven-calendar-day forecast {r.predicted_kg:.3f} kg; cutoff {frozen.as_of.isoformat()}, target {frozen.target_date.isoformat()}."
            statements.append({"text": text, "evidenceRefs": [forecast_ref, model_ref]})
    start = (frozen.target_date - timedelta(days=7)).isoformat()
    end = (frozen.target_date - timedelta(days=1)).isoformat()
    try:
        production = reference.production(scope, start, end, bearer)
        if production.get("estateId") != scope.estate_id or production.get("blockId") != scope.block_id or production.get("from") != start or production.get("to") != end or production.get("dataProvenance") != "SIMULATED":
            raise Unavailable("Mismatched production evidence")
        if production.get("projectionState") != "OBSERVED_ONLY" or production.get("sourceVersionSampleTruncated"):
            partial = True
            limitations.append("Current production evidence is incomplete or its source references are truncated.")
        else:
            link = {"kind": "PRODUCTION", "id": fingerprint(production), "href": analytics + "/production/daily?" + urlencode({"from": start, "to": end})}
            statements.append({"text": f"Current materialized production evidence covers {start} through {end}; it is separate from the earlier frozen forecast feature snapshot.", "evidenceRefs": [link]})
    except Unavailable:
        partial = True
        limitations.append("Current materialized production evidence is unavailable; no totals were invented.")
    if bool(environment_from) != bool(environment_to):
        raise ValueError("Both environment window bounds required")
    if environment_from:
        try:
            evidence = reference.environment(scope, environment_from, environment_to, bearer)
            if evidence.get("estateId") != scope.estate_id or evidence.get("blockId") != scope.block_id or evidence.get("from") != environment_from or evidence.get("to") != environment_to or evidence.get("dataProvenance") != "SIMULATED":
                raise Unavailable("Mismatched environmental evidence")
            if evidence.get("projectionState") != "OBSERVED_ONLY" or evidence.get("truncated") or evidence.get("availability") != "OBSERVED":
                partial = True
                limitations.append("Environmental evidence is missing, incomplete or truncated; no inferred coverage or causal claim.")
            else:
                statements.append({"text": f"Observed environmental evidence is available for UTC [{environment_from}, {environment_to}); no causal yield relationship is inferred.", "evidenceRefs": [{"kind": "ENVIRONMENT", "id": fingerprint(evidence), "href": analytics + "/environment/observations?" + urlencode({"from": environment_from, "to": environment_to})}]})
        except Unavailable:
            partial = True
            limitations.append("Environmental evidence unavailable; no observation or relationship was invented.")
    result = {"scope": {"estateId": scope.estate_id, "blockId": scope.block_id}, "provider": "DETERMINISTIC_EVIDENCE_V1",
              "templateVersion": "M4_EVIDENCE_TEMPLATES_V1", "status": "REFUSED" if not statements else "PARTIAL" if partial else "ANSWERED",
              "intent": intent, "statements": statements, "limitations": limitations}
    if sum(len(s["evidenceRefs"]) for s in statements) > 50 or len(json.dumps(result).encode()) > 65536:
        raise ValueError("Assistant admission limit")
    return result
