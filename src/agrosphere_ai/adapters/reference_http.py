"""Bounded, redirect-free calls to configured reference services under the caller's token."""
from datetime import datetime, timedelta
from urllib.parse import urlparse
import httpx
import jwt
from agrosphere_ai.application.ports.m4 import Principal, NotFound, Unavailable


def allowed_base(url):
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("Invalid reference URL")
    return url.rstrip("/")


def bounded_get(url, bearer=None):
    headers = {} if bearer is None else {"Authorization": "Bearer " + bearer}
    try:
        with httpx.stream("GET", url, headers=headers, timeout=5, follow_redirects=False, trust_env=False) as response:
            if response.status_code in (401, 403):
                raise PermissionError("Reference denied")
            if response.status_code == 404:
                raise NotFound("Unknown reference")
            if response.status_code != 200:
                raise Unavailable("Reference unavailable")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 1048576:
                    raise Unavailable("Oversized reference")
            import json
            return json.loads(raw)
    except (httpx.HTTPError, ValueError, RecursionError) as error:
        raise Unavailable("Reference unavailable") from error


class JwtVerifier:
    def __init__(self, issuer, jwks_url, audience="ai-service"):
        self.issuer = allowed_base(issuer)
        self.jwks_url = allowed_base(jwks_url)
        self.audience = audience

    def verify(self, bearer):
        if not bearer or len(bearer) > 16384:
            raise PermissionError("Authentication required")
        try:
            header = jwt.get_unverified_header(bearer)
            if header.get("alg") != "RS256" or header.get("crit") or not isinstance(header.get("kid"), str):
                raise ValueError("Unsupported token")
            keys = jwt.PyJWKSet.from_dict(bounded_get(self.jwks_url)).keys
            matching = [key for key in keys if key.key_id == header["kid"] and key.key_type == "RSA"]
            if len(matching) != 1:
                raise ValueError("Unknown signing key")
            claims = jwt.decode(bearer, matching[0].key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                                options={"require": ["exp", "iss", "aud", "sub", "iat"]})
            realm = claims.get("realm_access", {})
            roles = realm.get("roles", []) if isinstance(realm, dict) else None
            estates = claims.get("estate_ids", [])
            if not isinstance(roles, list) or not isinstance(estates, list) or any(not isinstance(v, str) for v in roles + estates):
                raise ValueError("Malformed trusted claims")
            from uuid import UUID
            if any(str(UUID(s)) != s for s in estates):
                raise ValueError("Malformed scope")
            return Principal(claims["sub"], frozenset(roles), frozenset(estates))
        except (jwt.PyJWTError, ValueError, TypeError, RecursionError) as error:
            raise PermissionError("Authentication required") from error


class ReferenceHttp:
    def __init__(self, estate_url, analytics_url):
        self.estate_url = allowed_base(estate_url)
        self.analytics_url = allowed_base(analytics_url)

    def require_active(self, scope, bearer):
        e = bounded_get(self.estate_url + "/api/v1/estates/" + scope.estate_id, bearer)
        b = bounded_get(self.estate_url + "/api/v1/blocks/" + scope.block_id, bearer)
        if e.get("id") != scope.estate_id or b.get("id") != scope.block_id or b.get("estateId") != scope.estate_id:
            raise Unavailable("Invalid reference response")
        if e.get("status") != "ACTIVE" or b.get("status") != "ACTIVE" or b.get("cropType") != "RUBBER":
            raise NotFound("Inactive rubber block")

    def prefix(self, scope):
        return self.analytics_url + "/api/v1/analytics/estates/" + scope.estate_id + "/blocks/" + scope.block_id

    def production(self, scope, from_date, to_date, bearer):
        from datetime import date
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if not 0 <= (end - start).days <= 62:
            raise ValueError("Invalid production evidence window")
        return bounded_get(self.prefix(scope) + "/production/daily?from=" + start.isoformat() + "&to=" + end.isoformat(), bearer)

    def environment(self, scope, from_time, to_time, bearer):
        from urllib.parse import urlencode
        start, end = datetime.fromisoformat(from_time), datetime.fromisoformat(to_time)
        if start.tzinfo is None or end.tzinfo is None or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or not timedelta(0) < end - start <= timedelta(days=1):
            raise ValueError("Invalid environmental window")
        return bounded_get(self.prefix(scope) + "/environment/observations?" + urlencode({"from": from_time, "to": to_time}), bearer)
