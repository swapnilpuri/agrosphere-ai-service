"""Foundation only. Domain routes and assistant endpoints are deferred."""
from fastapi import FastAPI

app = FastAPI(title="AgroSphere AI Service", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "UP"}
