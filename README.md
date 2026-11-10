# M4 bounded local engineering verified — 2026-11-10

AI 0.2.0 delivers exact observed dry/wet baselines/anomalies, immutable generated evaluation/model records, guarded seven-table journal/registry, conservative post-commit live availability and scoped closed-intent evidence composition. Business/consumer activation remains disabled by default. [Delivery and actual checks](../agrosphere-architecture/docs/milestones/M4_DELIVERY_VERIFICATION_2026_11_10.md) distinguish engineering verification from OPEN owner acceptance and synthetic/scientific/production limits. The current suite has 67 tests; real owned database/broker/Gateway/PKCE/Web and nonempty-upgrade/denial/recovery verification passed. Use only the owned Infrastructure verification harnesses; existing-target migration/activation is excluded. Older in-progress/foundation labels below are historical.

# M4 local delivery in progress — 2026-11-10

Owner-approved bounded M4 adds observed dry/wet mass baselines, immutable evaluation/model records, explicit point-in-time generated chronology and a closed-intent evidence composer. Business and broker activation default disabled. Engineering/owner acceptance remain separate; consult Architecture's checkpoint for actual checks. The historical foundation-only text below is superseded only for this approved scope.

Run `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (or `python -m pytest` in an independent installed environment), `python -m compileall -q src migrations`, and the packaged contract pin check `python -c "from agrosphere_ai.adapters.codec import validate_pins; validate_pins()"`. No ML extra is needed. Public contract snapshots are independently packaged under `src/agrosphere_ai/contracts`; builds never read sibling source paths.

Runtime and migration boundaries are reviewed in [M4_RUNTIME_REVIEW.md](docs/M4_RUNTIME_REVIEW.md). Do not run migration 0002 or enable business/consumer paths on an existing database. It requires a hashed owned manifest, companion marker and matching DB/principals. Generated import is an offline fixture command, never an HTTP endpoint. The independent Infrastructure `scripts/verify_m4.py` creates and removes its own complete generated project and demonstrates real signed identity/Gateway/API/Web behavior. It does not load existing `.env` files. Provider is DETERMINISTIC_EVIDENCE_V1; no LLM, cloud call or paid API. No REAL/production/Git/AEO action is authorized.

# agrosphere-ai-service

Forecasting, anomaly analysis, assistant orchestration, model evaluation/versioning and evidence-backed recommendations.

This is an independent project and Git root. Version: 0.1.0; use independent SemVer tags and CHANGELOG.md. License decision: LICENSE.md (all rights reserved pending selection).

See AGENTS.md for scope and engineering rules. This scaffold provides no business APIs or production features. Architecture lives in the sibling agrosphere-architecture repository; versioned API/event definitions live in agrosphere-contracts. Do not add path dependencies to sibling projects.

## Build and test

Python 3.12+. Create a local virtual environment, install with python -m pip install -e ".[test]", then python -m pytest. Run uvicorn agrosphere_ai.main:app --port 8086 for liveness only. No business routes or generated API docs are exposed. Authentication, inference and model storage are deferred.

Service-owned migrations: export DATABASE_URL, then alembic upgrade head when the local database is ready. Do not run migrations against another service database. Use .env.example as a template; variables must be exported explicitly. Optional ML dependencies install via .[ml]; Ollama integration is a future outbound adapter and is not installed or started. Domain, application ports and inbound/outbound adapters remain isolated.
