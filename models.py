from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ActionStep:
    id: Optional[int] = None
    type: str = "click"
    app_name: Optional[str] = None
    window_title: Optional[str] = None
    element_name: Optional[str] = None
    element_type: Optional[str] = None
    automation_id: Optional[str] = None
    class_name: Optional[str] = None
    parent_path: Optional[str] = None
    # Rich match signals captured at click time, so playback can self-heal toward
    # multiple cues instead of a single (x, y) or selector.
    element_rect: Optional[str] = None   # "l,t,r,b" screen rect of the element
    anchor: Optional[str] = None         # JSON: nearby stable anchor + click offset
    x: Optional[int] = None
    y: Optional[int] = None
    x_relative: Optional[float] = None
    y_relative: Optional[float] = None
    keys: Optional[str] = None
    text: Optional[str] = None
    scroll_dx: int = 0
    scroll_dy: int = 0
    delay_after: float = 0.0
    description: Optional[str] = None
    enabled: bool = True

    # Smart Wait — poll for the target element before running the step instead
    # of using the fixed delay. When enabled, delay_after is ignored at playback.
    smart_wait_enabled: bool = False
    smart_wait_timeout: float = 10.0
    smart_wait_on_timeout: str = "stop"  # "stop" or "skip"


@dataclass
class Trigger:
    hotkey: Optional[str] = None
    voice_phrase: Optional[str] = None


@dataclass
class Workflow:
    id: Optional[int] = None
    name: str = ""
    steps: list[ActionStep] = field(default_factory=list)
    trigger: Trigger = field(default_factory=Trigger)
    created_at: Optional[datetime] = None
    last_run: Optional[datetime] = None
    favorite: bool = False

    # Cached metrics filled by the lightweight list query (get_all_workflows),
    # so cards can show step count / duration without loading every step.
    _step_count: Optional[int] = field(default=None, repr=False, compare=False)
    _duration_secs: Optional[float] = field(default=None, repr=False, compare=False)

    @property
    def step_count(self) -> int:
        if self._step_count is not None:
            return self._step_count
        return len(self.steps)

    @property
    def duration_secs(self) -> float:
        if self._duration_secs is not None:
            return self._duration_secs
        return sum(s.delay_after for s in self.steps)

    @property
    def duration(self) -> str:
        """Total run time as ``m:ss``."""
        secs = int(round(self.duration_secs))
        return f"{secs // 60}:{secs % 60:02d}"

    @property
    def status(self) -> str:
        """UI status badge state for a saved workflow."""
        return "ready"
