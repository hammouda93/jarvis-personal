import unittest
from types import SimpleNamespace
from jarvis_agent.observation_readiness import bounded_observation
from jarvis_agent.mission_outcome_policy import failure_requires_review


class MissionContextRecoveryTests(unittest.TestCase):
    def test_retries_only_read_only_observation(self):
        states=iter([{"ready":False},{"ready":False},{"ready":True,"controls":[1]}])
        calls=[]
        result=bounded_observation(lambda: next(states),sleep=lambda s:calls.append(s))
        self.assertTrue(result["ready"])
        self.assertEqual(len(calls),2)

    def test_pending_is_not_misreported_as_ready(self):
        result=bounded_observation(lambda: {"ready":False},attempts=3,sleep=lambda s:None)
        self.assertIs(result["ready"],False)

    def test_generic_read_failure_vs_unknown_effect(self):
        read=SimpleNamespace(name="browser_observe_dom",success=False,
                             detail="RuntimeError: Invalid URL")
        nav=SimpleNamespace(name="open_url",success=True,
                            detail='{"dispatched":true,"verified":false}')
        self.assertFalse(failure_requires_review([nav,read]))
        unknown=SimpleNamespace(name="browser_click",success=False,
                               detail='{"outcome_unknown":true}')
        self.assertTrue(failure_requires_review([nav,unknown]))
        local=SimpleNamespace(name="open_url",success=False,
                             detail='{"dispatched":false,"outcome_unknown":false}')
        self.assertFalse(failure_requires_review([local]))
        opaque=SimpleNamespace(name="open_url",success=False,detail="error")
        self.assertTrue(failure_requires_review([opaque]))

if __name__ == "__main__":
    unittest.main()
