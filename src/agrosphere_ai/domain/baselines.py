"""M4 observed-mass policies. No I/O, ML packages or retrospective availability inference."""
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import re
from uuid import UUID
from zoneinfo import ZoneInfo

ZERO = Decimal("0.000")
KG = Decimal("0.001")
MODES = {"LIVE_FIRST_RECEIPT", "GENERATED_POINT_IN_TIME"}
TARGETS = ("WET", "DRY")


def canonical(value):
    def encode(v):
        if is_dataclass(v):
            return asdict(v)
        if isinstance(v, Decimal):
            return format(v, "f")
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        raise TypeError(type(v).__name__)
    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Explicit UTC instant required")
    return value.astimezone(timezone.utc)


def midnight(day, zone):
    # The following round trip rejects nonexistent local midnight (historical date-line changes).
    local = datetime.combine(day, time.min, ZoneInfo(zone))
    instant = local.astimezone(timezone.utc)
    if instant.astimezone(ZoneInfo(zone)).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError("Nonexistent local midnight")
    return instant


def cutoff_day(as_of, zone):
    day = utc(as_of).astimezone(ZoneInfo(zone)).date()
    if as_of != midnight(day, zone):
        raise ValueError("asOf must be estate-local midnight")
    if day.year == 1 or day.year == 9999:
        raise ValueError("Cutoff outside supported calendar arithmetic range")
    return day


@dataclass(frozen=True)
class Scope:
    estate_id: str
    block_id: str

    def __post_init__(self):
        UUID(self.estate_id)
        UUID(self.block_id)


@dataclass(frozen=True)
class Revision:
    event_id: str
    body_hash: str
    collection_id: str
    version: int
    scope: Scope
    collection_date: date
    estate_timezone: str
    kind: str
    status: str
    wet_kg: Decimal
    dry_kg: Decimal | None
    dry_exact_kg: Decimal | None
    calculation_policy: str | None
    source_at: datetime
    available_at: datetime
    first_received_at: datetime
    provenance: str = "SIMULATED"

    def __post_init__(self):
        UUID(self.event_id)
        UUID(self.collection_id)
        if not re.fullmatch(r"[0-9a-f]{64}", self.body_hash) or type(self.version) is not int or not 0 <= self.version <= 9223372036854775807:
            raise ValueError("Invalid source identity/version")
        ZoneInfo(self.estate_timezone)
        for instant in (self.source_at, self.available_at, self.first_received_at):
            utc(instant)
        if not isinstance(self.collection_date, date) or isinstance(self.collection_date, datetime):
            raise ValueError("Calendar collection date required")
        if self.provenance != "SIMULATED":
            raise ValueError("Only SIMULATED input is authorized")
        for mass in (self.wet_kg, self.dry_kg):
            if mass is not None and (not isinstance(mass, Decimal) or not mass.is_finite() or mass < 0 or mass > Decimal("999999.999") or mass != mass.quantize(KG)):
                raise ValueError("Exact source-rounded decimal kg required")
        if self.wet_kg <= 0 or self.status not in {"PENDING_DRC", "CALCULATED", "VOIDED"}:
            raise ValueError("Invalid source mass/state")
        if self.dry_kg is not None and self.dry_kg > self.wet_kg:
            raise ValueError("Dry mass exceeds wet")
        if self.status == "PENDING_DRC" and any(v is not None for v in (self.dry_kg, self.dry_exact_kg, self.calculation_policy)):
            raise ValueError("Pending DRC cannot invent dry mass")
        if self.status == "CALCULATED" and (self.dry_kg is None or self.dry_exact_kg is None or self.calculation_policy != "DRC_KG_V1"):
            raise ValueError("Unsupported or missing dry calculation evidence")
        if self.dry_exact_kg is not None and (not isinstance(self.dry_exact_kg, Decimal) or not self.dry_exact_kg.is_finite() or self.dry_exact_kg < 0 or self.dry_exact_kg > self.wet_kg or self.dry_exact_kg.as_tuple().exponent < -7 or self.calculation_policy != "DRC_KG_V1" or self.dry_exact_kg.quantize(KG, rounding=ROUND_HALF_UP) != self.dry_kg):
            raise ValueError("Inconsistent source dry calculation")
        valid = ((self.kind == "RECORDED" and self.version == 0 and self.status == "PENDING_DRC") or
                 (self.kind == "DRC_RECORDED" and self.version > 0 and self.status == "CALCULATED") or
                 (self.kind == "ADJUSTED" and self.version > 0 and self.status != "VOIDED") or
                 (self.kind == "VOIDED" and self.version > 0 and self.status == "VOIDED"))
        if not valid:
            raise ValueError("Event kind/version/state mismatch")


@dataclass(frozen=True)
class Conflict:
    event_id: str
    source_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class Dataset:
    scope: Scope
    estate_timezone: str
    mode: str
    revisions: tuple[Revision, ...]
    source_contracts: tuple[tuple[str, str], ...]
    generator: str
    seed: str
    clock_policy: str
    conflicts: tuple[Conflict, ...] = ()

    def __post_init__(self):
        if self.mode not in MODES or type(self.revisions) is not tuple or len(self.revisions) > 1000:
            raise ValueError("Unsupported chronology or admission limit")
        ZoneInfo(self.estate_timezone)
        if not self.source_contracts or type(self.source_contracts) is not tuple or any(type(pair) is not tuple for pair in self.source_contracts) or len(set(k for k, _ in self.source_contracts)) != len(self.source_contracts) or any(not re.fullmatch(r"[0-9a-f]{64}", h) for _, h in self.source_contracts):
            raise ValueError("Source contract fingerprints required")
        if not all((self.generator, self.seed, self.clock_policy)):
            raise ValueError("Dataset/clock identity required")
        if any(r.scope != self.scope or r.estate_timezone != self.estate_timezone for r in self.revisions):
            raise ValueError("No mixed scope or timezone dataset")
        if self.mode == "LIVE_FIRST_RECEIPT" and any(r.available_at != r.first_received_at for r in self.revisions):
            raise ValueError("Live availability must be actual first receipt")
        if type(self.conflicts) is not tuple:
            raise ValueError("Immutable conflicts required")
        for c in self.conflicts:
            UUID(c.event_id)
            utc(c.source_at)
            utc(c.available_at)

    @property
    def dataset_id(self):
        # Order-independent identity; identical redelivery with a later receipt has no dataset effect.
        unique = {}
        for r in self.revisions:
            key = (r.event_id, r.body_hash)
            if key not in unique or r.available_at < unique[key].available_at:
                unique[key] = r
        return fingerprint({"scope": self.scope, "zone": self.estate_timezone, "mode": self.mode,
                            "rows": sorted(unique.values(), key=lambda r: (r.event_id, r.body_hash)),
                            "contracts": sorted(self.source_contracts), "generator": self.generator,
                            "seed": self.seed, "clock": self.clock_policy,
                            "conflicts": sorted(set(self.conflicts), key=lambda c: (c.event_id, c.source_at, c.available_at))})


@dataclass(frozen=True)
class SourceRef:
    event_id: str
    collection_id: str
    version: int
    body_hash: str
    collection_date: date
    source_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class Snapshot:
    rows: tuple[Revision, ...]
    incomplete: bool


def snapshot(dataset, as_of):
    utc(as_of)
    by_event, groups = {}, {}
    incomplete = any(c.source_at < as_of and c.available_at < as_of for c in dataset.conflicts)
    # Both axes gate conflicts too: later discoveries never rewrite earlier frozen results.
    for row in sorted(dataset.revisions, key=lambda r: (r.available_at, r.event_id, r.body_hash)):
        if row.source_at >= as_of or row.available_at >= as_of:
            continue
        prior = by_event.get(row.event_id)
        if prior is not None:
            if prior.body_hash != row.body_hash or source_identity(prior) != source_identity(row):
                incomplete = True
            continue
        by_event[row.event_id] = row
        versions = groups.setdefault(row.collection_id, {})
        if row.version in versions:
            incomplete = True
        else:
            versions[row.version] = row
    rows = []
    for versions in groups.values():
        highest = max(versions)
        if len(versions) != highest + 1:
            incomplete = True
        latest = versions[highest]
        if any(r.status == "VOIDED" and r.version < highest for r in versions.values()) and latest.status != "VOIDED":
            incomplete = True
        rows.append(latest)
    return Snapshot(tuple(sorted(rows, key=lambda r: r.collection_id)), incomplete)


def source_identity(row):
    return (row.collection_id, row.version, row.scope, row.collection_date, row.estate_timezone,
            row.kind, row.status, row.wet_kg, row.dry_kg, row.dry_exact_kg, row.calculation_policy,
            row.source_at, row.provenance)


def ref(row):
    return SourceRef(row.event_id, row.collection_id, row.version, row.body_hash,
                     row.collection_date, row.source_at, row.available_at)


def bucket(snap, day, target):
    if snap.incomplete:
        return "INCOMPLETE_SOURCE", None, ()
    rows = tuple(r for r in snap.rows if r.collection_date == day)
    if not rows:
        return "INSUFFICIENT_HISTORY", None, ()
    sources = tuple(ref(r) for r in rows)
    if target == "DRY" and any(r.status == "PENDING_DRC" for r in rows):
        return "PENDING_DRC", None, sources
    value = sum((r.wet_kg if target == "WET" else r.dry_kg for r in rows if r.status != "VOIDED"), ZERO)
    return "ELIGIBLE", value, sources


@dataclass(frozen=True)
class TargetResult:
    target: str
    model_id: str
    comparator_model_id: str
    status: str
    predicted_kg: Decimal | None
    comparator_kg: Decimal | None
    sources: tuple[SourceRef, ...]
    anomaly_status: str
    actual_kg: Decimal | None
    residual_kg: Decimal | None
    threshold_kg: Decimal
    label_status: str
    label_sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ModelRecord:
    algorithm: str
    target: str
    feature_policy: str = "SOURCE_AND_AVAILABILITY_BEFORE_LOCAL_MIDNIGHT_V1"
    label_policy: str = "AS_KNOWN_AT_NEXT_LOCAL_MIDNIGHT_V1"
    rounding: str = "SOURCE_ROUNDED_SUM_OUTPUT_HALF_UP_3_V1"
    coverage: str = "OBSERVED_ONLY"
    uncertainty: str = "UNCALIBRATED"
    stage: str = "DEMO_ONLY"
    window_days: int = 7
    policy_version: str = "M4_BASELINES_V1"
    anomaly_policy: str = "ABSOLUTE_FROZEN_RESIDUAL_STRICT_GREATER_V1"
    threshold_kg: Decimal = Decimal("5.000")

    @property
    def model_id(self):
        return fingerprint(self)


def models(target):
    if target not in TARGETS:
        raise ValueError("Unknown target")
    threshold = Decimal("5.000" if target == "WET" else "1.000")
    return (ModelRecord(f"{target}_KG_MEAN_7_V1", target, threshold_kg=threshold),
            ModelRecord(f"{target}_KG_LAST_DAY_V1", target, window_days=1, threshold_kg=threshold))


@dataclass(frozen=True)
class Forecast:
    scope: Scope
    dataset_id: str
    mode: str
    as_of: datetime
    target_date: date
    estate_timezone: str
    label_as_of: datetime | None
    targets: tuple[TargetResult, ...]
    source_data_provenance: str = "SIMULATED"
    forecast_provenance: str = "PREDICTED"
    coverage: str = "OBSERVED_ONLY"
    uncertainty: str = "UNCALIBRATED"


def forecast(dataset, as_of, label_as_of=None):
    day = cutoff_day(as_of, dataset.estate_timezone)
    if label_as_of is not None and utc(label_as_of) != midnight(day + timedelta(days=1), dataset.estate_timezone):
        raise ValueError("labelAsOf must be the next local midnight")
    snap = snapshot(dataset, as_of)
    targets = []
    with localcontext() as ctx:
        ctx.prec = 50
        for target in TARGETS:
            model, comparator = models(target)
            buckets = [bucket(snap, day - timedelta(days=i), target) for i in range(7, 0, -1)]
            status = next((reason for reason in ("INCOMPLETE_SOURCE", "PENDING_DRC", "INSUFFICIENT_HISTORY") if any(b[0] == reason for b in buckets)), "ELIGIBLE")
            sources = tuple(s for b in buckets for s in b[2])
            mean = (sum((b[1] for b in buckets), ZERO) / 7).quantize(KG, rounding=ROUND_HALF_UP) if status == "ELIGIBLE" else None
            last = buckets[-1][1] if buckets[-1][0] == "ELIGIBLE" else None
            threshold = Decimal("5.000" if target == "WET" else "1.000")
            anomaly, actual, residual, label_status, label_sources = "NOT_EVALUATED", None, None, "NOT_EVALUATED", ()
            if label_as_of is not None:
                label_status, actual, label_sources = bucket(snapshot(dataset, label_as_of), day, target)
                if mean is None or actual is None:
                    anomaly = "UNSCORED"
                else:
                    residual = actual - mean
                    anomaly = "FLAGGED" if abs(residual) > threshold else "NOT_FLAGGED"
            targets.append(TargetResult(target, model.model_id, comparator.model_id, status, mean, last,
                                        sources, anomaly, actual, residual, threshold, label_status, label_sources))
    return Forecast(dataset.scope, dataset.dataset_id, dataset.mode, as_of, day, dataset.estate_timezone, label_as_of, tuple(targets))


@dataclass(frozen=True)
class Metric:
    target: str
    split: str
    algorithm: str
    scored: int
    withheld: int
    sum_absolute_error_kg: Decimal
    mae_kg: Decimal | None
    withheld_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Confusion:
    target: str
    split: str
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    unscored: int
    precision: Decimal | None
    recall: Decimal | None


@dataclass(frozen=True)
class Evaluation:
    scope: Scope
    dataset_id: str
    source_contracts: tuple[tuple[str, str], ...]
    start_date: date
    end_date: date
    split_boundaries: tuple[tuple[str, date, date], ...]
    models: tuple[ModelRecord, ...]
    metrics: tuple[Metric, ...]
    anomaly_counts: tuple[Confusion, ...]
    forecasts: tuple[Forecast, ...]
    synthetic_truth_hash: str
    mode: str = "GENERATED_POINT_IN_TIME"
    split_policy: str = "FORWARD_7_28_14_14_NO_TUNING_V1"
    stage: str = "DEMO_ONLY"

    @property
    def evaluation_id(self):
        return fingerprint(self)


def evaluate(dataset, start_date, synthetic_anomalies=()):
    """Freeze one 63-date generated protocol. Truth is fixture metadata, never a farm label."""
    if dataset.mode != "GENERATED_POINT_IN_TIME":
        raise ValueError("Synthetic protocol requires explicit generated chronology")
    end_date = start_date + timedelta(days=62)
    if any(not start_date <= r.collection_date <= end_date for r in dataset.revisions):
        raise ValueError("Dataset exceeds the 63-date protocol")
    truth = frozenset(synthetic_anomalies)
    if any(t not in TARGETS or not start_date + timedelta(days=7) <= d <= end_date for t, d in truth):
        raise ValueError("Invalid synthetic anomaly truth")
    boundaries = tuple((name, start_date + timedelta(days=lo), start_date + timedelta(days=hi)) for name, lo, hi in
                       (("TRAIN", 7, 34), ("VALIDATION", 35, 48), ("TEST", 49, 62)))
    forecasts = tuple(forecast(dataset, midnight(start_date + timedelta(days=i), dataset.estate_timezone),
                              midnight(start_date + timedelta(days=i + 1), dataset.estate_timezone)) for i in range(7, 63))
    metrics, counts = [], []
    for split, lo, hi in boundaries:
        selected = [f for f in forecasts if lo <= f.target_date <= hi]
        for target_index, target in enumerate(TARGETS):
            for algorithm, field in (("MEAN_7", "predicted_kg"), ("LAST_DAY", "comparator_kg")):
                errors, reasons = [], {}
                for f in selected:
                    r = f.targets[target_index]
                    predicted = getattr(r, field)
                    if predicted is None or r.actual_kg is None:
                        reason = r.status if predicted is None else r.label_status
                        reasons[reason] = reasons.get(reason, 0) + 1
                    else:
                        errors.append(abs(predicted - r.actual_kg))
                total = sum(errors, ZERO)
                mae = (total / len(errors)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) if errors else None
                metrics.append(Metric(target, split, algorithm, len(errors), len(selected) - len(errors), total, mae, tuple(sorted(reasons.items()))))
            tp = fp = tn = fn = unscored = 0
            for f in selected:
                state = f.targets[target_index].anomaly_status
                actual = (target, f.target_date) in truth
                if state == "UNSCORED":
                    unscored += 1
                elif state == "FLAGGED" and actual:
                    tp += 1
                elif state == "FLAGGED":
                    fp += 1
                elif actual:
                    fn += 1
                else:
                    tn += 1
            ratio = lambda a, b: (Decimal(a) / b).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) if b else None
            counts.append(Confusion(target, split, tp, fp, tn, fn, unscored, ratio(tp, tp + fp), ratio(tp, tp + fn)))
    return Evaluation(dataset.scope, dataset.dataset_id, tuple(sorted(dataset.source_contracts)), start_date, end_date, boundaries,
                      tuple(m for t in TARGETS for m in models(t)), tuple(metrics), tuple(counts), forecasts,
                      fingerprint(sorted((t, d.isoformat()) for t, d in truth)))
