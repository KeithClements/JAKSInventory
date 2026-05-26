"""
app/services/esn_lookup_service.py
=====================================
ESN (Engine Serial Number) lookup — Phase 3 feature stub.

Planned capability (from quoting_requirements.md):
  - Given an ESN, scrape / query vendor API for engine config
  - Return: manufacturer, model, displacement, years produced
  - Cache results in ESNLookup table to avoid redundant calls
  - Pre-populate quote / invoice engine fields from ESN

In Phase 1: interface defined, all methods raise NotImplementedError.
The quote screen will call check_esn() — when it returns None the UI
shows "ESN lookup not yet available" without changing the call site.

In Phase 3: implement against HHP or ATL Diesel lookup APIs.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.base import BaseService


@dataclass
class EngineSpec:
    esn: str
    manufacturer: str
    model: str
    displacement: str | None
    years_produced: str | None
    raw_response: str | None


class ESNLookupService(BaseService):

    def lookup(self, esn: str) -> EngineSpec | None:
        """
        Look up engine config by ESN.
        Checks cache (ESNLookup table) first; calls external API if not cached.
        Returns None if ESN not found or lookup service unavailable.
        Phase 1: always returns None.
        """
        raise NotImplementedError

    def get_cached(self, esn: str) -> EngineSpec | None:
        """Return cached ESN result without calling external API."""
        raise NotImplementedError

    def save_result(self, spec: EngineSpec) -> "ESNLookup":  # type: ignore[name-defined]
        """Persist an ESN lookup result to the cache table."""
        raise NotImplementedError

    def get_engine_configs(self, manufacturer: str | None = None) -> list["EngineConfig"]:  # type: ignore[name-defined]
        """
        Return engine configuration profiles for populating quote dropdowns.
        Used when ESN is not available — user selects manufacturer + model manually.
        """
        raise NotImplementedError
