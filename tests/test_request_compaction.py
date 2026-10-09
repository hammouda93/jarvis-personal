from __future__ import annotations

import copy
import json
import unittest

from jarvis_agent.request_compaction import compact_duplicate_observations


def observation(call_id, *, name="browser_find", success=True, detail=None):
    return {"role": "tool", "name": name, "tool_call_id": call_id, "content": json.dumps({
        "tool": name, "success": success, "detail": detail or {"visible_text": "data" * 600, "ref": "observed-1"}})}


class RequestCompactionTests(unittest.TestCase):
    def test_only_earlier_duplicate_is_replaced_and_transcript_and_latest_are_untouched(self):
        messages = [{"role": "system", "content": "Policy and mission goal"},
            {"role": "user", "content": "User constraints and clarifications"},
            {"role": "assistant", "tool_calls": [{"id": "one", "function": {"name": "browser_find", "arguments": "{}"}}]},
            observation("one"), observation("two")]
        original = copy.deepcopy(messages)
        result, saved = compact_duplicate_observations(messages)
        self.assertEqual(messages, original)
        self.assertEqual(result[-1], original[-1])
        self.assertEqual(result[:3], original[:3])
        self.assertEqual(result[-2]["tool_call_id"], "one")
        marker = json.loads(result[-2]["content"])
        self.assertTrue(marker["request_deduplicated"])
        self.assertEqual(marker["identical_observation_at_tool_call_id"], "two")
        self.assertGreater(saved, 1500)

    def test_mutations_unknown_tools_and_failed_results_are_never_compacted(self):
        for name, success in (("browser_click", True), ("mcp__server__read", True),
                              ("mission_checkpoint", True), ("browser_find", False)):
            messages = [observation("one", name=name, success=success), observation("two", name=name, success=success)]
            result, saved = compact_duplicate_observations(messages)
            self.assertEqual(result, messages)
            self.assertEqual(saved, 0)

    def test_different_refs_values_or_messages_prevent_deduplication(self):
        messages = [observation("one"), observation("two", detail={"visible_text": "data" * 600, "ref": "new-ref"})]
        result, saved = compact_duplicate_observations(messages)
        self.assertEqual(result, messages)
        self.assertEqual(saved, 0)

    def test_invalid_json_missing_ids_and_unknown_outcomes_remain_untouched(self):
        candidates = [observation("one"), observation("two")]
        for item in candidates:
            item["content"] = '{"success":true,"outcome_unknown":true,"data":"' + "x" * 1000 + '"}'
        self.assertEqual(compact_duplicate_observations(candidates), (candidates, 0))
        candidates[0]["content"] = "not json" * 300
        candidates[1].pop("tool_call_id")
        self.assertEqual(compact_duplicate_observations(candidates), (candidates, 0))
