"""Alpha Foundry V5: immutable research control plane for multi-mechanism alpha discovery."""

from .contracts import CandidateEvidence, DataDomain, EconomicEvidence, ExecutionStyle, ExperimentSpec, HypothesisSpec, ResearchStage, StatisticalEvidence, TimeWindow
from .labs.registry import LabRegistry
from .research_engine import ResearchEngine
from .validation import DEFAULT_POLICY, ValidationEngine

__all__ = ["CandidateEvidence", "DataDomain", "EconomicEvidence", "ExecutionStyle", "ExperimentSpec", "HypothesisSpec", "ResearchStage", "StatisticalEvidence", "TimeWindow", "LabRegistry", "ResearchEngine", "ValidationEngine", "DEFAULT_POLICY"]
