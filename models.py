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
