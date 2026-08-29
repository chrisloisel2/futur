from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from .hashing import sha256_obj


def _require_finite(**fields: float) -> None:
    bad = [name for name, value in fields.items() if not math.isfinite(float(value))]
    if bad:
        raise ValueError(f"non-finite required field(s): {', '.join(sorted(bad))}")


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

    def overlaps(self, other: TimeWindow) -> bool:
        return max(int(self.start_ns), int(other.start_ns)) < min(int(self.stop_ns), int(other.stop_ns))


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    family_id: str
    lab_id: str
    economic_source_id: str
    mechanism: str
    payer: str
    domains: tuple[DataDomain, ...]
    target_name: str
    horizon_ms: int
    feature_set_id: str
    model_family: str
    execution_style: ExecutionStyle
    max_trials: int
    max_lookback_ms: int
    confirmation_min_hours: float
    expected_sign: int = 1
    required_primary_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    support_symbols: tuple[str, ...] = ("SOLUSDT",)
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
    parent_experiment_id: str | None = None
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

    def __post_init__(self) -> None:
        if int(self.n) < 0:
            raise ValueError("n must be non-negative")
        _require_finite(
            ess=self.ess,
            ic=self.ic,
            q_value=self.q_value,
            block_p=self.block_p,
            dsr_probability=self.dsr_probability,
            pbo=self.pbo,
        )


@dataclass(frozen=True)
class EconomicEvidence:
    gross_edge_bps: float
    net_edge_bps: float
    delayed_entry_net_bps: float
    profit_factor: float
    max_drawdown: float
    capacity_usd: float
    top_contributors_removed_net_bps: float
    recent_period_net_bps: float
    paper_live_net_bps: float
    fill_rate: float
    realized_slippage_bps: float

    def __post_init__(self) -> None:
        # paper_live_net_bps is legitimately NaN before PAPER_LIVE evidence exists --
        # economic_gate() only checks it when require_paper=True. Every other field
        # is always computed by build_economic_evidence() and must never be NaN.
        # profit_factor may legitimately be +inf (zero losing trades) -- reject NaN
        # only, not infinity, there.
        if math.isnan(float(self.profit_factor)):
            raise ValueError("non-finite required field(s): profit_factor")
        _require_finite(
            gross_edge_bps=self.gross_edge_bps,
            net_edge_bps=self.net_edge_bps,
            delayed_entry_net_bps=self.delayed_entry_net_bps,
            max_drawdown=self.max_drawdown,
            capacity_usd=self.capacity_usd,
            top_contributors_removed_net_bps=self.top_contributors_removed_net_bps,
            recent_period_net_bps=self.recent_period_net_bps,
            fill_rate=self.fill_rate,
            realized_slippage_bps=self.realized_slippage_bps,
        )


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
    metadata: dict[str, float] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return sha256_obj(self)
