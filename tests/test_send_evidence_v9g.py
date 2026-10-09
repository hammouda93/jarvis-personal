"""A browser write alone never proves a message was delivered."""
from __future__ import annotations

import json
import unittest

from jarvis_agent.agent_runtime import _unverified_browser_send_claim
from jarvis_agent.native_tools import AgentActionResult


def action(name: str, *, verified: bool, postcondition: str):
    return AgentActionResult(
        name=name, success=True, message="ok",
        detail=json.dumps({"verified": verified, "postcondition": postcondition}))


class BrowserSendClaimTests(unittest.TestCase):
    def test_write_only_does_not_prove_quoted_message_sent(self):
        draft = action("browser_write", verified=True,
                       postcondition="element_value_observed")
        self.assertTrue(_unverified_browser_send_claim(
            'envoie "twahachtek barcha rouhy"', "Message envoyé.", [draft]))

    def test_write_only_does_not_prove_send_on_explicit_chat(self):
        draft = action("browser_write", verified=True,
                       postcondition="element_value_observed")
        self.assertTrue(_unverified_browser_send_claim(
            "envoie le message", "Je l'ai envoyé.", [draft]))

    def test_verified_composer_submission_is_not_blocked(self):
        sent = action("browser_click", verified=True,
                      postcondition="focused_editable_value_cleared")
        self.assertFalse(_unverified_browser_send_claim(
            "envoie le message", "Message envoyé.", [sent]))

    def test_unrelated_write_not_claimed_as_send(self):
        draft = action("browser_write", verified=True,
                       postcondition="element_value_observed")
        self.assertFalse(_unverified_browser_send_claim(
            "écris ce texte", "Texte saisi.", [draft]))

    def test_send_claim_without_any_action_is_not_evidence(self):
        self.assertTrue(_unverified_browser_send_claim(
            "envoie un message", "Message envoyé.", []))


if __name__ == "__main__":
    unittest.main()
