"""A long cumulative suite is healthy while a stalled single test is not."""
import io
import unittest
from unittest.mock import patch

from scripts.run_final_regression import ProgressTestResult


class RegressionWatchdogTests(unittest.TestCase):
    def test_timer_tracks_individual_progress_and_still_diagnoses_a_stalled_test(self):
        for durations, expected in (([120, 120], []), ([181], [181])):
            with self.subTest(durations=durations):
                now, deadline, fired, arms = [0], [None], [], []
                def arm(seconds, *, repeat):
                    self.assertFalse(repeat)
                    deadline[0] = now[0] + seconds
                    arms.append(seconds)
                def cancel():
                    deadline[0] = None
                def work(seconds):
                    now[0] += seconds
                    if deadline[0] is not None and now[0] >= deadline[0]:
                        fired.append(now[0])
                suite = unittest.TestSuite([unittest.FunctionTestCase(lambda s=s: work(s)) for s in durations])
                with patch("scripts.run_final_regression.faulthandler.dump_traceback_later", side_effect=arm), patch(
                    "scripts.run_final_regression.faulthandler.cancel_dump_traceback_later", side_effect=cancel):
                    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=ProgressTestResult).run(suite)
                self.assertTrue(result.wasSuccessful())
                self.assertEqual(result.testsRun, len(durations))
                self.assertEqual(arms, [180] * len(durations))
                self.assertEqual(fired, expected)
                self.assertIsNone(deadline[0])
