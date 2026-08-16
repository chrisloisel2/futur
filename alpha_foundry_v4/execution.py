from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MakerEconomics:
    expected_edge_bps: float
    spread_capture_bps: float
    predictive_markout_bps: float
    adverse_selection_bps: float
    fee_bps: float
    missed_opportunity_bps: float
    fill_probability: float


def expected_maker_edge_bps(spread_capture_bps: float, predictive_markout_bps: float, adverse_selection_bps: float, fee_bps: float, missed_opportunity_bps: float, fill_probability: float) -> MakerEconomics:
    fill = min(max(float(fill_probability), 0.0), 1.0)
    conditional = float(spread_capture_bps) + float(predictive_markout_bps) - float(adverse_selection_bps) - float(fee_bps)
    edge = fill * conditional - (1.0 - fill) * float(missed_opportunity_bps)
    return MakerEconomics(expected_edge_bps=float(edge), spread_capture_bps=float(spread_capture_bps), predictive_markout_bps=float(predictive_markout_bps), adverse_selection_bps=float(adverse_selection_bps), fee_bps=float(fee_bps), missed_opportunity_bps=float(missed_opportunity_bps), fill_probability=fill)


def expected_taker_edge_bps(predictive_move_bps: float, half_spread_bps: float, fee_bps: float, slippage_bps: float, latency_decay_bps: float) -> float:
    return float(predictive_move_bps - half_spread_bps - fee_bps - slippage_bps - latency_decay_bps)
