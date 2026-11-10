"""Composition root for explicitly enabled owned M4 fixtures."""
from dataclasses import dataclass
import os
from agrosphere_ai.adapters.codec import validate_pins
from agrosphere_ai.adapters.ownership import Guard
from agrosphere_ai.adapters.reference_http import JwtVerifier, ReferenceHttp
from agrosphere_ai.adapters.store import Store


@dataclass
class Runtime:
    guard: object
    store: object
    references: object
    jwt: object


def configured_runtime():
    if os.environ.get("AI_BUSINESS_ENABLED") != "true":
        return None
    validate_pins()
    guard = Guard(os.environ["AI_GENERATED_MANIFEST"], os.environ["AI_GENERATED_MANIFEST_SHA256"], os.environ["AI_DATABASE_URL"])
    store = Store(guard)
    with store.connection():
        pass
    return Runtime(guard, store, ReferenceHttp(os.environ["ESTATE_SERVICE_URL"], os.environ["ANALYTICS_SERVICE_URL"]),
                   JwtVerifier(os.environ["OIDC_ISSUER_URI"], os.environ["OIDC_JWK_SET_URI"]))
