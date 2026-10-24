from fastapi.testclient import TestClient
from agrosphere_ai.main import app

client = TestClient(app)

def test_liveness():
    assert client.get("/health/live").json() == {"status": "UP"}

def test_business_and_docs_routes_are_absent():
    for path in ("/api/v1/forecasts", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
