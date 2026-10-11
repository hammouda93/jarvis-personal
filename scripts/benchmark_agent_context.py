"""Isolated deterministic payload benchmark, NOT API cost or live acceptance."""
import copy
import json
from pathlib import Path
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_final_regression import regression_environment


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main():
    with tempfile.TemporaryDirectory(prefix="context-benchmark-") as directory:
        os.environ.update(regression_environment(Path(directory)))
        from jarvis_agent.agent_runtime import GroqResponsesAgent, _effective_system_instructions
        from jarvis_agent.foundation_tools import FoundationToolAdapter
        from jarvis_agent.native_tools import NATIVE_TOOLS
        from jarvis_agent.request_context import RequestContext
        adapter = FoundationToolAdapter(NATIVE_TOOLS, browser=object(), computer=object())
        agent = GroqResponsesAgent(adapter)
        adapter.browser_mode = True
        schemas = agent._tool_definitions()
        messages = [{"role": "system", "content": "Fixture mission policy"},
                    {"role": "user", "content": "Observe without editing; retain 67890"}]
        for index in range(8):
            messages.extend([{"role": "assistant", "tool_calls": [{"id": str(index), "type": "function",
                "function": {"name": "browser_observe_dom", "arguments": '{"tab_id":7}'}}]},
                {"role": "tool", "name": "browser_observe_dom", "tool_call_id": str(index),
                 "content": json.dumps({"success": True, "detail": {"tab": {"tab_id": 7},
                    "visible_text": f"Synthetic observation {index}. " * 260}})}])
        original = copy.deepcopy(messages)
        context = RequestContext(enabled=True)
        compact, saved = context.compact(messages)
        selected = context.prepare_tools(schemas, browser_mode=True)
        original_policy_chars = len(_effective_system_instructions())
        os.environ["JARVIS_EFFICIENT_CONTEXT_ENABLED"] = "1"
        efficient_policy_chars = len(_effective_system_instructions())
        assert messages == original
        print(json.dumps({"fixture_only": True, "llm_calls_for_compaction": 0,
            "provider_http_requests_performed": 0, "messages_before_chars": size(messages),
            "messages_after_chars": size(compact), "saved_content_chars": saved,
            "tools_before_count": len(schemas), "tools_after_count": len(selected),
            "tools_before_chars": size(schemas), "tools_after_chars": size(selected),
            "base_policy_before_chars": original_policy_chars, "base_policy_after_chars": efficient_policy_chars,
            "full_transcript_unchanged": True, "recoverable_observations": len(context.evidence)}, indent=2))


if __name__ == "__main__":
    main()
