from __future__ import annotations

from pydantic import BaseModel

from domain.events.raw import BaseEvent


class MacroTickEvent(BaseEvent):
    macro_symbol: str
    value: float
