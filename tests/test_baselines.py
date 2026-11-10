from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from agrosphere_ai.domain.baselines import (
    Dataset, Revision, Scope, canonical, cutoff_day, evaluate, fingerprint,
    forecast, midnight, models, snapshot,
)

START = date(2026, 8, 1)
SCOPE = Scope(str(uuid5(NAMESPACE_URL, "estate")), str(uuid5(NAMESPACE_URL, "block")))
CONTRACTS = (("collection-v2-test-fixture", "a" * 64),)


def row(i, version=0, wet="10.000", dry=None, **kw):
    day = START + timedelta(days=i)
    stamp = midnight(day, "UTC") + timedelta(hours=12, seconds=version)
    args = dict(event_id=str(uuid5(NAMESPACE_URL, f"event:{i}:{version}")),
                body_hash=fingerprint([i, version, wet, dry]),
                collection_id=str(uuid5(NAMESPACE_URL, f"collection:{i}")), version=version,
                scope=SCOPE, collection_date=day, estate_timezone="UTC",
                kind="RECORDED" if version == 0 else "DRC_RECORDED", status="PENDING_DRC" if dry is None else "CALCULATED",
                wet_kg=Decimal(wet), dry_kg=None if dry is None else Decimal(dry),
                dry_exact_kg=None if dry is None else Decimal(dry), calculation_policy=None if dry is None else "DRC_KG_V1",
                source_at=stamp, available_at=stamp, first_received_at=stamp)
    args.update(kw)
    return Revision(**args)


def dataset(rows, **kw):
    args = dict(scope=SCOPE, estate_timezone="UTC", mode="GENERATED_POINT_IN_TIME", revisions=tuple(rows),
                source_contracts=CONTRACTS, generator="PYTEST_V1", seed="0", clock_policy="GENERATED_AVAILABILITY_V1")
    args.update(kw)
    return Dataset(**args)


def history(n=8, amounts=None):
    rows = []
    for i in range(n):
        wet = f"{10 + i if amounts is None else amounts[i]:.3f}"
        rows.extend((row(i, wet=wet), row(i, 1, wet=wet, dry="2.000")))
    return rows


def test_independent_oracle_mean_last_day_exact_dry_and_sources():
    f = forecast(dataset(history()), midnight(START + timedelta(days=7), "UTC"))
    assert f.targets[0].predicted_kg == Decimal("13.000")  # (10+11+12+13+14+15+16)/7
    assert f.targets[0].comparator_kg == Decimal("16.000")
    assert f.targets[1].predicted_kg == Decimal("2.000")
    assert len(f.targets[0].sources) == 7
    assert f.coverage == "OBSERVED_ONLY" and f.uncertainty == "UNCALIBRATED"
    assert f.forecast_provenance == "PREDICTED" and f.source_data_provenance == "SIMULATED"


def test_mean_rounds_half_up_once_and_preserves_source_rounding():
    rows = []
    for i, wet in enumerate(["1.001"] * 6 + ["1.002", "1.001"]):
        rows.extend((row(i, wet=wet), row(i, 1, wet=wet, dry="0.100")))
    f = forecast(dataset(rows), midnight(START + timedelta(days=7), "UTC"))
    assert f.targets[0].predicted_kg == Decimal("1.001")


@pytest.mark.parametrize("target,actual,equal,over", [(0, "15.000", "NOT_FLAGGED", "15.001"), (1, "3.000", "NOT_FLAGGED", "3.001")])
def test_anomaly_strict_threshold_and_frozen_forecast(target, actual, equal, over):
    rows = history(amounts=[10] * 8)
    kwargs = dict(wet_kg=Decimal(actual)) if target == 0 else dict(dry_kg=Decimal(actual), dry_exact_kg=Decimal(actual))
    rows[-1] = replace(rows[-1], **kwargs)
    ds = dataset(rows)
    as_of = midnight(START + timedelta(days=7), "UTC")
    before = forecast(ds, as_of)
    labeled = forecast(ds, as_of, as_of + timedelta(days=1))
    assert before.targets[target].predicted_kg == labeled.targets[target].predicted_kg
    assert labeled.targets[target].anomaly_status == equal
    kwargs = dict(wet_kg=Decimal(over)) if target == 0 else dict(dry_kg=Decimal(over), dry_exact_kg=Decimal(over))
    rows[-1] = replace(rows[-1], **kwargs)
    assert forecast(dataset(rows), as_of, as_of + timedelta(days=1)).targets[target].anomaly_status == "FLAGGED"
    assert before.targets[target].anomaly_status == "NOT_EVALUATED"


def test_negative_residual_is_flagged():
    ds = dataset(history(amounts=[20] * 7 + [10]))
    f = forecast(ds, midnight(START + timedelta(days=7), "UTC"), midnight(START + timedelta(days=8), "UTC"))
    assert f.targets[0].residual_kg == Decimal("-10.000") and f.targets[0].anomaly_status == "FLAGGED"


def test_missing_none_and_observed_void_zero_are_distinct():
    rows = history()
    cutoff = midnight(START + timedelta(days=7), "UTC")
    missing = dataset([r for r in rows if r.collection_date != START])
    assert forecast(missing, cutoff).targets[0].predicted_kg is None
    assert forecast(missing, cutoff).targets[0].status == "INSUFFICIENT_HISTORY"
    for i in range(7):
        rows.append(row(i, 2, kind="VOIDED", status="VOIDED"))
    zero = forecast(dataset(rows), cutoff)
    assert zero.targets[0].predicted_kg == zero.targets[1].predicted_kg == Decimal("0.000")


def test_pending_drc_withholds_only_dry_and_never_fills_with_zero():
    ds = dataset([row(i) for i in range(8)])
    f = forecast(ds, midnight(START + timedelta(days=7), "UTC"), midnight(START + timedelta(days=8), "UTC"))
    assert f.targets[0].status == "ELIGIBLE"
    assert f.targets[1].status == "PENDING_DRC" and f.targets[1].anomaly_status == "UNSCORED"


@pytest.mark.parametrize("axis", ["source_at", "available_at"])
def test_strict_cutoff_both_axes_late_backfills_and_versions(axis):
    cutoff = midnight(START + timedelta(days=7), "UTC")
    rows = history()
    rows[1] = replace(rows[1], **{axis: cutoff})
    f = forecast(dataset(rows), cutoff)
    assert f.targets[0].predicted_kg == Decimal("13.000")
    assert f.targets[1].status == "PENDING_DRC"
    rows[1] = replace(rows[1], **{axis: cutoff - timedelta(microseconds=1)})
    assert forecast(dataset(rows), cutoff).targets[1].status == "ELIGIBLE"


def test_later_correction_cannot_rewrite_prior_forecast_or_frozen_label():
    rows = history()
    cutoff = midnight(START + timedelta(days=7), "UTC")
    before = forecast(dataset(rows), cutoff, cutoff + timedelta(days=1))
    rows.append(row(0, 2, wet="900.000", dry="300.000", kind="ADJUSTED", available_at=cutoff + timedelta(days=2)))
    after = forecast(dataset(rows), cutoff, cutoff + timedelta(days=1))
    assert before.targets == after.targets


def test_duplicate_reorder_and_older_redelivery_are_deterministic():
    rows = history()
    ds = dataset(rows)
    reordered = dataset(list(reversed(rows)) + [rows[0]])
    assert ds.dataset_id == reordered.dataset_id
    cutoff = midnight(START + timedelta(days=7), "UTC")
    assert forecast(ds, cutoff) == forecast(reordered, cutoff)
    later = replace(rows[0], available_at=rows[0].available_at + timedelta(hours=1))
    assert dataset([later] + rows).dataset_id == ds.dataset_id
    assert forecast(dataset([later] + rows), cutoff) == forecast(ds, cutoff)


@pytest.mark.parametrize("conflict", ["same-event", "same-version", "hole", "resurrection"])
def test_conflicts_gaps_and_tombstones_withhold_scope_wide(conflict):
    rows = history()
    if conflict == "same-event":
        rows.append(replace(rows[0], body_hash="b" * 64))
    elif conflict == "same-version":
        rows.append(replace(rows[1], event_id=str(uuid5(NAMESPACE_URL, "other"))))
    elif conflict == "hole":
        rows.remove(rows[0])
    else:
        rows.extend([row(0, 2, status="VOIDED", kind="VOIDED"), row(0, 3, dry="2.000")])
    f = forecast(dataset(rows), midnight(START + timedelta(days=7), "UTC"))
    assert all(r.status == "INCOMPLETE_SOURCE" and r.predicted_kg is None for r in f.targets)


def test_future_conflict_discovery_is_not_past_knowledge():
    rows = history()
    cutoff = midnight(START + timedelta(days=7), "UTC")
    original = forecast(dataset(rows), cutoff)
    rows.append(replace(rows[0], body_hash="c" * 64, available_at=cutoff))
    assert forecast(dataset(rows), cutoff).targets == original.targets


@pytest.mark.parametrize("day,expected_hours", [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_dst_next_midnight_is_calendar_not_24_hours(day, expected_hours):
    a = midnight(day, "America/Chicago")
    b = midnight(day + timedelta(days=1), "America/Chicago")
    assert (b - a).total_seconds() / 3600 == expected_hours
    assert cutoff_day(a, "America/Chicago") == day
    ds = dataset([], estate_timezone="America/Chicago")
    forecast(ds, a, b)
    with pytest.raises(ValueError):
        forecast(ds, a, a + timedelta(days=1))


def test_invalid_naive_or_nonmidnight_cutoff_and_missing_label():
    ds = dataset(history(7))
    a = midnight(START + timedelta(days=7), "UTC")
    for invalid in (a.replace(tzinfo=None), a + timedelta(seconds=1)):
        with pytest.raises(ValueError):
            forecast(ds, invalid)
    f = forecast(ds, a, a + timedelta(days=1))
    assert f.targets[0].actual_kg is None and f.targets[0].anomaly_status == "UNSCORED"


def test_model_and_record_immutability_and_policy_identity():
    mean, last = models("WET")
    assert mean.model_id != last.model_id
    assert models("DRY")[0].model_id != mean.model_id
    assert replace(mean, threshold_kg=Decimal("6.000")).model_id != mean.model_id
    with pytest.raises(FrozenInstanceError):
        mean.stage = "PRODUCTION"


def test_forward_protocol_independent_metric_oracle_worse_than_comparator():
    ds = dataset(history(63))  # Linear wet-mass ramp: mean lags by 4 kg, last day by 1 kg.
    ev = evaluate(ds, START)
    assert len(ev.forecasts) == 56 and len(ev.metrics) == 12
    assert [m.scored for m in ev.metrics] == [28, 28, 28, 28, 14, 14, 14, 14, 14, 14, 14, 14]
    wet_mean = next(m for m in ev.metrics if m.target == "WET" and m.split == "TEST" and m.algorithm == "MEAN_7")
    wet_last = next(m for m in ev.metrics if m.target == "WET" and m.split == "TEST" and m.algorithm == "LAST_DAY")
    assert wet_mean.sum_absolute_error_kg == Decimal("56.000") and wet_mean.mae_kg == Decimal("4.000000")
    assert wet_last.sum_absolute_error_kg == Decimal("14.000") and wet_last.mae_kg == Decimal("1.000000")
    assert evaluate(dataset(list(reversed(ds.revisions))), START).evaluation_id == ev.evaluation_id
    assert ev.models[0].stage == "DEMO_ONLY" and len(ev.synthetic_truth_hash) == 64


def test_future_test_labels_do_not_enter_earlier_features_or_validation():
    rows = history(63)
    ev = evaluate(dataset(rows), START)
    rows[-1] = replace(rows[-1], wet_kg=Decimal("500.000"))
    changed = evaluate(dataset(rows), START)
    assert [f.targets for f in ev.forecasts[:-1]] == [f.targets for f in changed.forecasts[:-1]]
    assert [m for m in ev.metrics if m.split != "TEST"] == [m for m in changed.metrics if m.split != "TEST"]
    assert ev.forecasts[-1].targets[0].predicted_kg == changed.forecasts[-1].targets[0].predicted_kg
    assert ev.evaluation_id != changed.evaluation_id


def test_no_scored_rows_or_anomaly_denominator_is_unavailable_not_perfect():
    ev = evaluate(dataset([]), START)
    assert all(m.scored == 0 and m.mae_kg is None and m.withheld > 0 for m in ev.metrics)
    assert all(c.precision is None and c.recall is None and c.unscored > 0 for c in ev.anomaly_counts)


def test_synthetic_anomaly_truth_is_separate_and_identified():
    rows = history(63, [10] * 62 + [30])
    ev = evaluate(dataset(rows), START, (("WET", START + timedelta(days=62)),))
    c = next(c for c in ev.anomaly_counts if c.target == "WET" and c.split == "TEST")
    assert c.true_positive == 1 and c.false_positive == 0 and c.precision == Decimal("1.000000")
    assert ev.synthetic_truth_hash != evaluate(dataset(rows), START).synthetic_truth_hash


def test_dataset_and_source_boundaries_reject_real_mixed_scope_or_forged_live_clock():
    r = row(0)
    with pytest.raises(ValueError):
        replace(r, provenance="REAL")
    with pytest.raises(ValueError):
        dataset([r], scope=Scope(str(uuid5(NAMESPACE_URL, "other-estate")), SCOPE.block_id))
    with pytest.raises(ValueError):
        dataset([replace(r, available_at=r.available_at - timedelta(seconds=1))], mode="LIVE_FIRST_RECEIPT")
    with pytest.raises(ValueError):
        dataset([r] * 1001)
    with pytest.raises(ValueError):
        evaluate(dataset([r], mode="LIVE_FIRST_RECEIPT"), START)
    with pytest.raises(ValueError):
        evaluate(dataset([row(63)]), START)


@pytest.mark.parametrize("change", [dict(wet_kg=Decimal("1.0001")), dict(dry_kg=Decimal("11.000")),
    dict(status="CALCULATED"), dict(body_hash="bad"), dict(version=True), dict(source_at=datetime(2026, 1, 1))])
def test_invalid_source_precision_state_identity_and_clock(change):
    with pytest.raises(ValueError):
        replace(row(0), **change)


def test_canonical_json_contains_no_nan_and_has_reproducible_fingerprint():
    assert canonical({"kg": Decimal("2.500")}) == '{"kg":"2.500"}'
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    with pytest.raises(ValueError):
        canonical({"value": float("nan")})
