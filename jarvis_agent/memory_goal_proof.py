"""Independent persistent-memory goal verifier for explicit user memory writes.

Reads local SQLite again. Does not trust a model assertion or a tool success
flag and grants no permission to write memory.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryWriteProof:
    memory_id: int
    proof_ref: str


@dataclass(frozen=True)
class MemoryWriteReceipt:
    """Versioned local write receipt; parsing never dispatches another write."""
    memory_id: int
    raw_sha256: str
    db_recorded_at: str
    receipt_version: int = 1
    source: str = "sqlite_exact_raw_readback"
    verified: bool = True
    dispatched: bool = True
    status: str = "raw_saved"

    def as_json(self):
        from dataclasses import asdict
        return json.dumps(asdict(self), ensure_ascii=False)


def parse_memory_write_receipt(detail):
    """Accept V1 and pre-V1 JSON; reject malformed IDs, hashes and versions."""
    try:
        data = json.loads(detail) if isinstance(detail, str) else detail
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("receipt_version", 0)
    memory_id = data.get("memory_id")
    digest = data.get("raw_sha256")
    recorded = data.get("db_recorded_at", "")
    if (type(version) is not int or version not in (0, 1)
            or type(memory_id) is not int or memory_id <= 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(recorded, str) or len(recorded) > 100
            or data.get("source") != "sqlite_exact_raw_readback"
            or data.get("outcome_unknown") is True or data.get("dispatched") is False
            or data.get("verified") is not True
            or (version == 1 and (not recorded or data.get("dispatched") is not True))):
        return None
    return MemoryWriteReceipt(memory_id, digest, recorded, version,
                              status=str(data.get("status", "raw_saved"))[:120])


def readback_memory_write(raw, detail, store):
    """Independently check literal content, digest, identity and creation time."""
    receipt = parse_memory_write_receipt(detail)
    if receipt is None or store is None:
        return None
    digest = hashlib.sha256(str(raw or "").strip().encode("utf-8")).hexdigest()
    if receipt.raw_sha256 != digest:
        return None
    try:
        stored = store.get_memory(receipt.memory_id)
    except Exception:
        return None
    if (stored is None or stored.id != receipt.memory_id
            or stored.content != str(raw or "").strip()
            or hashlib.sha256(stored.content.encode("utf-8")).hexdigest() != digest
            or (receipt.db_recorded_at and receipt.db_recorded_at != stored.created_at)):
        return None
    return stored


def foundation_memory_store(runtime):
    """Walk known wrappers without accessing unrelated external data."""
    visited = set()
    obj = runtime
    for _ in range(18):
        if obj is None or id(obj) in visited:
            break
        visited.add(id(obj))
        tools = getattr(obj, "tools", None)
        if tools is not None:
            store = getattr(tools, "memory", None)
            if store is not None and callable(getattr(store, "get_memory", None)):
                return store
            if tools is not obj and id(tools) not in visited:
                found = foundation_memory_store_shallow(tools, visited)
                if found is not None:
                    return found
        obj = getattr(obj, "delegate", None)
    return None


def foundation_memory_store_shallow(obj, visited):
    for _ in range(12):
        if obj is None or id(obj) in visited:
            return None
        visited.add(id(obj))
        store = getattr(obj, "memory", None)
        if store is not None and callable(getattr(store, "get_memory", None)):
            return store
        obj = getattr(obj, "delegate", None)
    return None


def verify_explicit_memory_write(goal, actions, store):
    from .memory_router import MemoryRouter
    if store is None or MemoryRouter().decide(goal).kind != "write":
        return None
    actions = tuple(actions or ())
    if len(actions) != 1:
        return None
    action = actions[0]
    if (getattr(action, "name", "") != "remember_information"
            or not getattr(action, "success", False)):
        return None
    raw = str(goal or "").strip()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    stored = readback_memory_write(raw, getattr(action, "detail", ""), store)
    if stored is None:
        return None
    memory_id = stored.id
    identity = hashlib.sha256(
        (str(memory_id) + "\0" + digest + "\0"
         + str(stored.created_at)).encode("utf-8")
    ).hexdigest()[:24]
    return MemoryWriteProof(
        memory_id=memory_id, proof_ref="sqlite_memory_verified:" + identity,
    )
