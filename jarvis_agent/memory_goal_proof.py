"""Independent persistent-memory goal verifier for explicit user memory writes.

Reads local SQLite again. Does not trust a model assertion or a tool success
flag and grants no permission to write memory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryWriteProof:
    memory_id: int
    proof_ref: str


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
    try:
        data = json.loads(str(getattr(action, "detail", "") or ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    memory_id = data.get("memory_id")
    if (type(memory_id) is not int or memory_id <= 0
            or data.get("verified") is not True
            or data.get("source") != "sqlite_exact_raw_readback"):
        return None
    raw = str(goal or "").strip()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if data.get("raw_sha256") != digest:
        return None
    try:
        stored = store.get_memory(memory_id)
    except Exception:
        return None
    if stored is None or stored.content != raw:
        return None
    identity = hashlib.sha256(
        (str(memory_id) + "\0" + digest + "\0"
         + str(stored.created_at)).encode("utf-8")
    ).hexdigest()[:24]
    return MemoryWriteProof(
        memory_id=memory_id, proof_ref="sqlite_memory_verified:" + identity,
    )
