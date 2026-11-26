"""P1H — split-pin tests for the loop_engine_plan extraction.

Pins the Phase-1 extraction itself (not behaviour — behaviour is
pinned by test_p1h_loop_engine_characterization.py):

  * services.loop_engine_plan exists and defines _generate_plan,
    _CostCapPaused, PLAN_LLM_TIMEOUT_S;
  * services.loop_engine re-exports the SAME objects (identity, not
    copies), so every existing call site keeps working;
  * loop_engine.py no longer DEFINES any of the three names.

Run with the characterization file:
  python3 -m unittest discover -s backend/tests -p "test_p1h_*.py" -t backend
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.loop_engine as le  # noqa: E402
import services.loop_engine_plan as plan_mod  # noqa: E402


class TestPlanSplit(unittest.TestCase):
    def test_new_module_defines_the_names(self):
        self.assertTrue(callable(plan_mod._generate_plan))
        self.assertTrue(issubclass(plan_mod._CostCapPaused, Exception))
        self.assertEqual(plan_mod.PLAN_LLM_TIMEOUT_S, 30)

    def test_loop_engine_reexports_identical_objects(self):
        self.assertIs(le._generate_plan, plan_mod._generate_plan)
        self.assertIs(le._CostCapPaused, plan_mod._CostCapPaused)
        self.assertEqual(le.PLAN_LLM_TIMEOUT_S,
                         plan_mod.PLAN_LLM_TIMEOUT_S)

    def test_generate_plan_lives_in_new_module(self):
        self.assertEqual(le._generate_plan.__module__,
                         "services.loop_engine_plan")
        self.assertEqual(plan_mod._generate_plan.__module__,
                         "services.loop_engine_plan")

    def test_loop_engine_no_longer_defines_them(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "loop_engine.py")).read()
        self.assertNotIn("async def _generate_plan", src)
        self.assertNotIn("class _CostCapPaused", src)
        self.assertNotIn("PLAN_LLM_TIMEOUT_S = int(", src)
        # ...but it DOES re-export them.
        self.assertIn("from services.loop_engine_plan import", src)

    def test_new_module_defines_them(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "loop_engine_plan.py")).read()
        self.assertIn("async def _generate_plan", src)
        self.assertIn("class _CostCapPaused", src)
        self.assertIn("PLAN_LLM_TIMEOUT_S = int(", src)

    def test_no_other_loop_engine_names_moved(self):
        # The extraction moved exactly three names. Spot-check that the
        # big pieces are still where they were.
        for name in ("LoopEngine", "LoopState", "resume_stale",
                     "sweep_expired_awaiting_confirmations",
                     "lookup_or_rehydrate", "register", "deregister",
                     "lookup", "reset_registry", "PHASE_TIMEOUTS_S",
                     "MAX_SELF_HEALS", "STALE_AFTER_S"):
            self.assertTrue(hasattr(le, name), name)
            self.assertFalse(hasattr(plan_mod, name)
                             and getattr(plan_mod, name) is getattr(le, name)
                             and name not in ("PHASE_TIMEOUTS_S",),
                             f"{name} leaked into plan module")


if __name__ == "__main__":
    unittest.main()
