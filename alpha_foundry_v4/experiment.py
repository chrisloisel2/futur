from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class ResearchStage(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    DEV_DISCOVERY = "DEV_DISCOVERY"
    INDEPENDENT_CONFIRMATION = "INDEPENDENT_CONFIRMATION"
    EXECUTION_ECONOMICS = "EXECUTION_ECONOMICS"
    PAPER_LIVE = "PAPER_LIVE"
    PORTFOLIO_ADMISSION = "PORTFOLIO_ADMISSION"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ExperimentProtocol:
    experiment_id: str
    mechanism_id: str
    stage: ResearchStage
    feature_names: Sequence[str]
    target_name: str
    horizons_ms: Sequence[int]
    data_window_id: str
    preregistered: bool
    parent_experiment_id: str = ""


_ALLOWED = {
    ResearchStage.HYPOTHESIS: {ResearchStage.DEV_DISCOVERY, ResearchStage.REJECTED},
    ResearchStage.DEV_DISCOVERY: {ResearchStage.INDEPENDENT_CONFIRMATION, ResearchStage.REJECTED},
    ResearchStage.INDEPENDENT_CONFIRMATION: {ResearchStage.EXECUTION_ECONOMICS, ResearchStage.REJECTED},
    ResearchStage.EXECUTION_ECONOMICS: {ResearchStage.PAPER_LIVE, ResearchStage.REJECTED},
    ResearchStage.PAPER_LIVE: {ResearchStage.PORTFOLIO_ADMISSION, ResearchStage.REJECTED},
    ResearchStage.PORTFOLIO_ADMISSION: set(),
    ResearchStage.REJECTED: set(),
}


def validate_stage_transition(previous: ExperimentProtocol, new: ExperimentProtocol, require_new_window: bool = True) -> None:
    if new.stage not in _ALLOWED[previous.stage]:
        raise ValueError("invalid research stage transition: %s -> %s" % (previous.stage, new.stage))
    if new.mechanism_id != previous.mechanism_id:
        raise ValueError("mechanism_id cannot change across a promotion transition")
    if new.stage != ResearchStage.REJECTED and not new.preregistered:
        raise ValueError("promoted experiment must be preregistered before data inspection")
    if require_new_window and new.stage == ResearchStage.INDEPENDENT_CONFIRMATION and new.data_window_id == previous.data_window_id:
        raise ValueError("independent confirmation requires a new data window")
