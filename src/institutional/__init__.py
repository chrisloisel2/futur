"""
src/institutional — INSTITUTIONAL_ENGINE
Moteur algorithmique institutionnel parallèle à TRM_EVENT_ENGINE.
"""
from src.institutional.contracts import (
    SignalFrame,
    PortfolioState,
    RiskState,
    Position,
    DataQualityReport,
    ExperimentRecord,
    RobustnessScore,
    Opportunity,
    ReasonCode,
    OPPORTUNITY_COLUMNS,
    ENGINE_STATUSES,
    DECISION_ZONES,
    OPPORTUNITY_DIRECTIONS,
    STATUS_SIZE_FRACTION,
    SIGNAL_FRAME_COLUMNS,
    validate_signal_frame_df,
)

__all__ = [
    "SignalFrame",
    "PortfolioState",
    "RiskState",
    "Position",
    "DataQualityReport",
    "ExperimentRecord",
    "RobustnessScore",
    "Opportunity",
    "ReasonCode",
    "OPPORTUNITY_COLUMNS",
    "ENGINE_STATUSES",
    "DECISION_ZONES",
    "OPPORTUNITY_DIRECTIONS",
    "STATUS_SIZE_FRACTION",
    "SIGNAL_FRAME_COLUMNS",
    "validate_signal_frame_df",
]
