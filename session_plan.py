"""Work/break sequencing independent of the Windows UI and countdown clock."""
from dataclasses import asdict, dataclass


PHASE_LABELS = {"work": "Work", "short_break": "Short break", "long_break": "Long break"}
SETTING_LIMITS = {"work_minutes": (1, 240), "short_break_minutes": (1, 120),
                  "long_break_minutes": (1, 240), "long_break_every": (1, 24)}


@dataclass
class SessionPlan:
    work_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    long_break_every: int = 4
    phase: str = "work"
    completed_sessions: int = 0
    phase_recorded: bool = False

    @classmethod
    def from_state(cls, state, completed=False):
        plan = cls()
        if not isinstance(state, dict):
            plan.phase_recorded = completed
            return plan
        for field, (minimum, maximum) in SETTING_LIMITS.items():
            value = state.get(field)
            if type(value) is int and minimum <= value <= maximum:
                setattr(plan, field, value)
        phase = state.get("phase")
        if isinstance(phase, str) and phase in PHASE_LABELS:
            plan.phase = phase
        count = state.get("completed_sessions")
        if type(count) is int and 0 <= count <= 1_000_000_000:
            plan.completed_sessions = count
        recorded = state.get("phase_recorded")
        # Old zero-valued timers must not be counted again just by reopening.
        plan.phase_recorded = completed or (recorded if type(recorded) is bool else False)
        return plan

    def to_state(self):
        return asdict(self)

    def configure(self, **settings):
        for field, value in settings.items():
            if field not in SETTING_LIMITS:
                raise ValueError("Unknown session setting")
            minimum, maximum = SETTING_LIMITS[field]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{field.replace('_', ' ')} must be {minimum}–{maximum}")
        for field, value in settings.items():
            setattr(self, field, value)

    def duration(self):
        field = {"work": "work_minutes", "short_break": "short_break_minutes",
                 "long_break": "long_break_minutes"}[self.phase]
        return getattr(self, field) * 60

    def select_phase(self, phase):
        if not isinstance(phase, str) or phase not in PHASE_LABELS:
            raise ValueError("Unknown timer phase")
        self.phase = phase
        self.phase_recorded = False

    def finish_phase(self):
        if self.phase_recorded:
            return False
        if self.phase == "work":
            self.completed_sessions = min(1_000_000_000, self.completed_sessions + 1)
        self.phase_recorded = True
        return True

    def next_phase(self):
        if self.phase != "work":
            return "work"
        if (self.phase_recorded and self.completed_sessions > 0
                and self.completed_sessions % self.long_break_every == 0):
            return "long_break"
        return "short_break"

    def phase_title(self):
        count = self.completed_sessions
        if self.phase == "work":
            number = max(0, count - int(self.phase_recorded)) % self.long_break_every + 1
            return f"Work · Session {number}/{self.long_break_every}"
        number = count % self.long_break_every + 1
        return f"{PHASE_LABELS[self.phase]} · Next session {number}/{self.long_break_every}"
