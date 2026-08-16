from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Sequence, Tuple


class DataDomain(str, Enum):
    BOOK = "book"
    TRADE = "trade"
    DERIVATIVES = "derivatives"
    SPOT = "spot"
    WALLET = "wallet"
    OPTIONS = "options"
    ONCHAIN = "onchain"
    EVENT = "event"
    EXECUTION = "execution"
    CROSS_ASSET = "cross_asset"


class ModelFamily(str, Enum):
    INFORMATION_SCREEN = "information_screen"
    SURVIVAL = "survival"
    POINT_PROCESS = "point_process"
    STATE_SPACE = "state_space"
    ERROR_CORRECTION = "error_correction"
    HMM = "hmm"
    CHANGE_POINT = "change_point"
    DEEP_LOB = "deep_lob"
    TEMPORAL_TRANSFORMER = "temporal_transformer"
    GRAPH_TEMPORAL = "graph_temporal"
    HIERARCHICAL_BAYES = "hierarchical_bayes"
    EXECUTION_MODEL = "execution_model"


class ExecutionStyle(str, Enum):
    TAKER = "taker"
    MAKER = "maker"
    HYBRID = "hybrid"
    HEDGE = "hedge"
    FILTER = "filter"
    INVENTORY_SKEW = "inventory_skew"


@dataclass(frozen=True)
class MechanismSpec:
    lab_id: str
    name: str
    hypothesis: str
    payer: str
    domains: Tuple[DataDomain, ...]
    targets: Tuple[str, ...]
    horizons_ms: Tuple[int, ...]
    model_families: Tuple[ModelFamily, ...]
    execution_styles: Tuple[ExecutionStyle, ...]
    independence_key: str
    required_symbols: int = 2
    notes: str = ""


@dataclass(frozen=True)
class PromotionEvidence:
    pit_clean: bool
    independent_forward: bool
    dsr: float
    pbo: float
    cost_x2_positive: bool
    delayed_entry_positive: bool
    top_contributors_removed_positive: bool
    same_sign_halves: bool
    recent_period_not_destructive: bool
    sleeve_pf: float
    capacity_usd: float
    paper_live_positive: bool
    net_edge_bps: float
    mechanism_confirmed: bool
    marginal_portfolio_positive: bool = False
    metadata: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AlphaCandidate:
    candidate_id: str
    mechanism_id: str
    independence_key: str
    pnl_series_name: str
    evidence: PromotionEvidence
    tags: Sequence[str] = field(default_factory=tuple)
