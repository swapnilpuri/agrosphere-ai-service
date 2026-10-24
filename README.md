# agrosphere-ai-service

Forecasting, anomaly analysis, assistant orchestration, model evaluation/versioning and evidence-backed recommendations.

This is an independent project and Git root. Version: 0.1.0; use independent SemVer tags and CHANGELOG.md. License decision: LICENSE.md (all rights reserved pending selection).

See AGENTS.md for scope and engineering rules. This scaffold provides no business APIs or production features. Architecture lives in the sibling agrosphere-architecture repository; versioned API/event definitions live in agrosphere-contracts. Do not add path dependencies to sibling projects.

## Build and test

Python 3.12+. Create a local virtual environment, install with python -m pip install -e ".[test]", then python -m pytest. Run uvicorn agrosphere_ai.main:app --port 8086 for liveness only. No business routes or generated API docs are exposed. Authentication, inference and model storage are deferred.

Service-owned migrations: export DATABASE_URL, then alembic upgrade head when the local database is ready. Do not run migrations against another service database. Use .env.example as a template; variables must be exported explicitly. Optional ML dependencies install via .[ml]; Ollama integration is a future outbound adapter and is not installed or started. Domain, application ports and inbound/outbound adapters remain isolated.
