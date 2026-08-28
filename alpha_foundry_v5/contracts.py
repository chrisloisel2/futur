from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple

from .hashing import sha256_obj


class ResearchStage(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    DEV_DISCOVERY = "DEV_DISCOVERY"
    INDEPENDENT_CONFIRMATION = "INDEPENDENT_CONFIRMATION"
    EXECUTION_ECONOMICS = "EXECUTION_ECONOMICS"
    PAPER_LIVE = "PAPER_LIVE"
    PORTFOLIO_ADMISSION = "PORTFOLIO_ADMISSION"
    REJECTED = "REJECTED"


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


class ExecutionStyle(str, Enum):
    TAKER = "taker"
    MAKER = "maker"
    HYBRID = "hybrid"
    HEDGE = "hedge"
    FILTER = "filter"
    INVENTORY_SKEW = "inventory_skew"


@dataclass(frozen=True)
class TimeWindow:
    start_ns: int
    stop_ns: int

    def __post_init__(self) -> None:
        if int(self.start_ns) <= 0 or int(self.stop_ns) <= int(self.start_ns):
            raise ValueError("invalid time window")

    @property
    def duration_s(self) -> float:
        return float((int(self.stop_ns) - int(self.start_ns)) / 1e9)

    def overlaps(self, other: "TimeWindow") -> bool:
        return max(int(self.start_ns), int(other.start_ns)) < min(int(self.stop_ns), int(other.stop_ns))


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    family_id: str
    lab_id: str
    economic_source_id: str
    mechanism: str
    payer: str
    domains: Tuple[DataDomain, ...]
    target_name: str
    horizon_ms: int
    feature_set_id: str
    model_family: str
    execution_style: ExecutionStyle
    max_trials: int
    max_lookback_ms: int
    confirmation_min_hours: float
    expected_sign: int = 1
    required_primary_symbols: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    support_symbols: Tuple[str, ...] = ("SOLUSDT",)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.hypothesis_id or not self.family_id or not self.lab_id:
            raise ValueError("hypothesis identifiers are required")
        if int(self.horizon_ms) <= 0 or int(self.max_trials) <= 0 or int(self.max_lookback_ms) < 0:
            raise ValueError("invalid hypothesis numeric contract")
        if float(self.confirmation_min_hours) <= 0:
            raise ValueError("confirmation_min_hours must be positive")
        if int(self.expected_sign) not in {-1, 1}:
            raise ValueError("expected_sign must be -1 or +1")

    @property
    def digest(self) -> str:
        return sha256_obj(self)


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    hypothesis_digest: str
    stage: ResearchStage
    dataset_manifest_digest: str
    window: TimeWindow
    code_commit: str
    seed: int
    label_horizon_ms: int
    lookback_ms: int
    model_params: Mapping[str, object] = field(default_factory=dict)
    parent_experiment_id: Optional[str] = None
    search_family_id: str = ""

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.hypothesis_digest or not self.dataset_manifest_digest:
            raise ValueError("experiment identifiers are required")
        if not self.code_commit:
            raise ValueError("code_commit is required")
        if int(self.label_horizon_ms) <= 0 or int(self.lookback_ms) < 0:
            raise ValueError("invalid experiment horizons")

    @property
    def digest(self) -> str:
        return sha256_obj(self)


@dataclass(frozen=True)
class FoldResult:
    fold_id: str
    train_window: TimeWindow
    test_window: TimeWindow
    selected_config_digest: str
    n_test: int
    ic: float
    net_return: float
    gross_return: float


@dataclass(frozen=True)
class StatisticalEvidence:
    n: int
    ess: float
    ic: float
    q_value: float
    block_p: float
    dsr_probability: float
    pbo: float
    same_sign_halves: bool
    all_primary_symbols_pass: bool
    independent_window: bool
    reverse_dominant: bool = False


@dataclass(frozen=True)
class EconomicEvidence:
    gross_edge_bps: float
    net_edge_bps: float
    net_edge_cost_x2_bps: float
    delayed_entry_net_bps: float
    profit_factor: float
    max_drawdown: float
    capacity_usd: float
    top_contributors_removed_net_bps: float
    recent_period_net_bps: float
    paper_live_net_bps: float
    fill_rate: float
    realized_slippage_bps: float


@dataclass(frozen=True)
class CandidateEvidence:
    candidate_id: str
    hypothesis_digest: str
    discovery_experiment_digest: str
    confirmation_experiment_digest: str
    statistical: StatisticalEvidence
    economic: EconomicEvidence
    pnl_artifact: str
    predictions_artifact: str
    metrics_artifact: str
    metadata: Dict[str, float] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return sha256_obj(self)
