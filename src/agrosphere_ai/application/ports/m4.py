"""M4 ports: adapters own authentication, storage and reference HTTP."""
from dataclasses import dataclass
from typing import Protocol
from agrosphere_ai.domain.baselines import Dataset, Scope


class Unavailable(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    estate_ids: frozenset[str]


def authorize(principal, scope):
    global_role = principal.roles & {"PLATFORM_ADMIN", "PLANTATION_ADMIN"}
    assigned = principal.roles & {"ESTATE_ADMIN", "ESTATE_MANAGER", "AUDITOR"} and scope.estate_id in principal.estate_ids
    if not global_role and not assigned:
        raise PermissionError("M4 scope denied")


class ChronologyReader(Protocol):
    def dataset(self, scope: Scope, dataset_id: str, mode: str) -> Dataset: ...


class ActiveReference(Protocol):
    def require_active(self, scope: Scope, bearer: str) -> None: ...


class EvidenceProvider(Protocol):
    def production(self, scope: Scope, from_date: str, to_date: str, bearer: str) -> dict: ...
    def environment(self, scope: Scope, from_time: str, to_time: str, bearer: str) -> dict: ...
