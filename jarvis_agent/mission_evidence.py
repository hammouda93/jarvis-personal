"""Projection of actual native receipts into the existing durable checkpoint.

Facts help resolve preconditions. They do not certify a goal, authorize an
action, or turn historical UI references into current executable targets.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import PureWindowsPath, PurePosixPath


def ledger_facts(receipts, *, task_id, goal_revision):
    facts = []
    for receipt in receipts:
        if not receipt.get("usable"):
            continue
        try:
            data = json.loads(receipt["detail"])
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        source = str(receipt["name"])
        seq = receipt["sequence"]
        def record(kind, identity, observed, *, reliability="observed"):
            if not identity:
                return
            fid = "fact_" + hashlib.sha256(
                (task_id + ":" + str(seq) + ":" + kind).encode()).hexdigest()[:20]
            facts.append({"id": fid, "task_id": task_id, "goal_revision": goal_revision,
                          "source": source, "recorded_at": receipt["recorded_at"],
                          "kind": kind, "identity": identity, "observed": observed,
                          "reliability": reliability, "scope": "mission_only",
                          "goal_proof": False})
        if data.get("stale_ref") is True and data.get("dispatched") is False:
            record("expired_reference", data.get("ref"), {"executable": False})
        # MCP output is external data, NEVER proof of the user's overall goal.
        # Only a small trusted local dispatch receipt enters durable memory.
        # We intentionally discard the remote response body, structured data,
        # credentials and tool arguments from the mission evidence ledger.
        if source.startswith("mcp__") and data.get("dispatched") is True:
            origin = data.get("source")
            if isinstance(origin, dict):
                server = origin.get("server_id")
                tool = origin.get("tool_name")
                attempt = origin.get("attempt_id")
                if (isinstance(server, str) and isinstance(tool, str)
                        and 0 < len(server) <= 36 and 0 < len(tool) <= 100
                        and type(attempt) is int and attempt > 0
                        and origin.get("trust") == "external_unverified"
                        and source.startswith("mcp__" + server + "__")):
                    record("mcp_invocation", f"{server}:{tool}:{attempt}", {
                        "server_id": server, "tool_name": tool,
                        "attempt_id": attempt,
                        "reported_success": bool(receipt["success"]),
                        "outcome_unknown": data.get("outcome_unknown") is True,
                        "independently_verified": False,
                    }, reliability="external_unverified")
        if not receipt["success"] or data.get("outcome_unknown") is True:
            continue
        path = data.get("selected_path") or data.get("path")
        if source in {"find_file", "open_file", "verify_file_artifact"} and isinstance(path, str):
            # Handle Windows paths on both Windows and Linux without guessing.
            parser = PureWindowsPath(path) if "\\" in path else PurePosixPath(path)
            record("file_identity", path, {"path": path, "filename": parser.name,
                                         "directory": str(parser.parent),
                                         "opened": source == "open_file",
                                         "read_only": source != "open_file"})
        observation = data.get("post_observation") or data.get("observation") or data
        if isinstance(observation, dict):
            window = observation.get("window")
            if isinstance(window, dict) and observation.get("observation_id"):
                identity = window.get("handle") or window.get("id") or window.get("title")
                record("window_observation", identity, {
                    "window": window, "observation_id": observation["observation_id"],
                    "historical_refs_not_executable": True,
                })
            tab = observation.get("tab")
            if isinstance(tab, dict) and tab.get("tab_id") is not None:
                record("tab_observation", tab["tab_id"], {
                    "url": tab.get("url"), "observation_id": observation.get("observation_id"),
                    "historical_refs_not_executable": True,
                })
        if data.get("verified") is True and data.get("postcondition"):
            record("verified_postcondition", data.get("scope") or data.get("source_ref")
                   or path or data.get("tab_id") or source, {
                       "postcondition": data["postcondition"],
                   }, reliability="verified_action_effect")
    return facts


def merge_facts(existing, additions):
    merged = {str(f["id"]): f for f in existing if isinstance(f, dict) and f.get("id")}
    for fact in additions:
        merged[fact["id"]] = fact
    # Full ordered action receipts stay in TaskGraphStore. Compact projection
    # is bounded and never discards the authoritative safety receipt history.
    durable = [f for f in merged.values() if f.get("kind") in {"file_identity", "environment"}]
    transient = [f for f in merged.values() if f.get("kind") not in {"file_identity", "environment"}]
    selected = durable[-32:] + transient[-96:]
    return selected


def reconcile_preconditions(state):
    """Typed bindings resolve facts only; arbitrary goals stay unverified."""
    plan = state.expected_state.get("semantic_contract")
    if not isinstance(plan, dict):
        return
    metadata = plan.get("metadata") or {}
    facts = state.observed_state.get("evidence_ledger") or []
    by_role = {}
    for fact in facts:
        values = fact.get("observed") or {}
        if fact.get("kind") == "file_identity":
            for role, field in (("file_path", "path"), ("filename", "filename"), ("directory", "directory")):
                if values.get(field):
                    by_role.setdefault(role, {})[str(values[field])] = fact["id"]
        elif fact.get("kind") == "environment" and values.get("operating_system"):
            by_role.setdefault("operating_system", {})[values["operating_system"]] = fact["id"]
        elif fact.get("kind") == "window_observation":
            title = (values.get("window") or {}).get("title")
            if title:
                by_role.setdefault("window_title", {})[title] = fact["id"]
    unresolved = list(plan.get("unresolved") or [])
    evidence = state.observed_state.setdefault("plan_evidence", {})
    resolutions = state.observed_state.setdefault("resolved_preconditions", [])
    for kind, label in (("preconditions", "question"), ("fact_bindings", "requirement")):
        for binding in metadata.get(kind) or []:
            values = by_role.get(binding.get("fact"), {})
            if len(values) != 1:
                continue
            fid = next(iter(values.values()))
            if kind == "preconditions" and binding.get(label) in unresolved:
                unresolved.remove(binding[label])
                resolutions.append({"question": binding[label], "fact_id": fid,
                                    "source": "typed_native_fact_binding"})
            elif kind == "fact_bindings":
                requirement = binding.get(label)
                required = {r for step in plan.get("steps") or []
                            for r in step.get("required_evidence") or []}
                if requirement in required:
                    evidence[requirement] = "mission_fact:" + fid
    plan["unresolved"] = unresolved
    control = state.expected_state.get("goal_control")
    if isinstance(control, dict):
        control["unresolved"] = unresolved
