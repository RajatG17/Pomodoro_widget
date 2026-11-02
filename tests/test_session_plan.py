import unittest

from session_plan import SessionPlan


class SessionPlanTests(unittest.TestCase):
    def test_default_work_short_and_long_break_cycle(self):
        plan = SessionPlan()
        self.assertEqual(plan.duration(), 1500)
        for number in range(1, 5):
            self.assertTrue(plan.finish_phase())
            self.assertEqual(plan.completed_sessions, number)
            self.assertEqual(plan.next_phase(), "long_break" if number == 4 else "short_break")
            plan.select_phase(plan.next_phase())
            self.assertEqual(plan.duration(), 900 if number == 4 else 300)
            plan.finish_phase()
            self.assertEqual(plan.completed_sessions, number)
            plan.select_phase(plan.next_phase())
            self.assertEqual(plan.phase, "work")
        self.assertIn("Session 1/4", plan.phase_title())

    def test_finish_is_idempotent(self):
        plan = SessionPlan()
        self.assertTrue(plan.finish_phase())
        self.assertFalse(plan.finish_phase())
        self.assertEqual(plan.completed_sessions, 1)

    def test_skip_does_not_count_as_finished_work(self):
        plan = SessionPlan()
        plan.select_phase(plan.next_phase())
        self.assertEqual(plan.phase, "short_break")
        self.assertEqual(plan.completed_sessions, 0)

    def test_custom_durations_and_long_break_interval(self):
        plan = SessionPlan()
        plan.configure(work_minutes=50, short_break_minutes=10, long_break_minutes=20, long_break_every=2)
        self.assertEqual(plan.duration(), 3000)
        plan.finish_phase()
        plan.select_phase("work")
        plan.finish_phase()
        self.assertEqual(plan.next_phase(), "long_break")
        plan.select_phase(plan.next_phase())
        self.assertEqual(plan.duration(), 1200)

    def test_invalid_configuration_is_transactional(self):
        plan = SessionPlan()
        with self.assertRaises(ValueError):
            plan.configure(work_minutes=50, short_break_minutes=0)
        self.assertEqual(plan.work_minutes, 25)
        for value in (0, -1, 1000, True, "four"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    plan.configure(long_break_every=value)

    def test_state_roundtrip_preserves_phase_settings_and_completion_marker(self):
        plan = SessionPlan(work_minutes=40, phase="long_break", completed_sessions=4, phase_recorded=True)
        restored = SessionPlan.from_state(plan.to_state())
        self.assertEqual(restored, plan)
        self.assertFalse(restored.finish_phase())

    def test_legacy_completed_timer_is_not_counted_again(self):
        plan = SessionPlan.from_state(None, completed=True)
        self.assertFalse(plan.finish_phase())
        self.assertEqual(plan.completed_sessions, 0)

    def test_reopened_zero_timer_is_not_counted_even_with_stale_completion_marker(self):
        plan = SessionPlan.from_state({"phase_recorded": False, "completed_sessions": 3}, completed=True)
        self.assertFalse(plan.finish_phase())
        self.assertEqual(plan.completed_sessions, 3)

    def test_malformed_state_uses_valid_defaults(self):
        for state in (None, [], "bad", {"phase": [], "completed_sessions": -1,
                                       "work_minutes": True, "long_break_every": "four"}):
            with self.subTest(state=state):
                self.assertEqual(SessionPlan.from_state(state), SessionPlan())

    def test_unknown_phase_is_rejected_without_changing_plan(self):
        plan = SessionPlan()
        for phase in ("unknown", None, []):
            with self.subTest(phase=phase):
                with self.assertRaises(ValueError):
                    plan.select_phase(phase)
                self.assertEqual(plan.phase, "work")


if __name__ == "__main__":
    unittest.main()
