"""Explicit read-only connector resolution after a persistent-memory miss.

Adapters are registered by trusted application wiring, never loaded from an
LLM response or import string in user/page content.
"""
from __future__ import annotations

from .kernel_contracts import RiskLevel


class MemoryConnectorResolver:
    def __init__(self):
        self._readers = {}

    def register(self, connector_id, reader):
        if not connector_id or not callable(reader):
            raise ValueError("explicit_read_only_reader_required")
        self._readers[connector_id] = reader

    def register_gateway(self, gateway, connector_id, capability):
        spec = gateway.registry.get(connector_id)
        cap = next((cap for cap in spec.capabilities if cap.name == capability),None) if spec else None
        if not cap or cap.requires_confirmation or cap.risk in {RiskLevel.EXTERNAL_SIDE_EFFECT,RiskLevel.DESTRUCTIVE}:
            raise ValueError("memory_connector_must_be_read_only")
        def reader(query):
            result = gateway.execute(connector_id=connector_id,capability=capability,arguments={"query":query})
            if not result.success:
                raise RuntimeError(result.error or result.message)
            # Capability adapters provide explicit facts, never arbitrary page instructions.
            facts = (result.data or {}).get("facts",[])
            if not isinstance(facts,list) or not all(isinstance(fact,str) for fact in facts):
                raise ValueError("connector_facts_must_be_string_list")
            return facts
        self.register(connector_id,reader)

    def __call__(self, query):
        facts = []
        for reader in self._readers.values():
            facts.extend(reader(query))
        return facts


MEMORY_CONNECTORS = MemoryConnectorResolver()
