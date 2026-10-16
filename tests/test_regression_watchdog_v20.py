"""A long cumulative suite is healthy while a stalled single test is not."""
import io
import unittest
from functools import partial

from scripts.run_final_regression import ProgressTestResult, ProgressWatchdog


class RegressionWatchdogTests(unittest.TestCase):
    def test_timer_tracks_individual_progress_and_still_diagnoses_a_stalled_test(self):
        now = [0]
        watchdog = ProgressWatchdog(clock=lambda: now[0])
        for _ in range(2):
            watchdog.arm()
            now[0] += 120
            self.assertFalse(watchdog.expired())
            watchdog.disarm()
        self.assertEqual(now[0], 240)
        watchdog.arm()
        now[0] += 181
        self.assertTrue(watchdog.expired())
        self.assertFalse(watchdog.expired())
        watchdog.arm()
        watchdog.disarm()
        now[0] += 200
        self.assertFalse(watchdog.expired())

    def test_many_fast_tests_reuse_one_monitor_and_close_it_after_the_run(self):
        watchdog = ProgressWatchdog()
        identities = set()
        def work():
            self.assertTrue(watchdog.thread.is_alive())
            identities.add(watchdog.thread.ident)
        suite = unittest.TestSuite([unittest.FunctionTestCase(work) for _ in range(250)])
        result = unittest.TextTestRunner(stream=io.StringIO(),
            resultclass=partial(ProgressTestResult, watchdog=watchdog)).run(suite)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 250)
        self.assertEqual(len(identities), 1)
        self.assertFalse(watchdog.thread.is_alive())
