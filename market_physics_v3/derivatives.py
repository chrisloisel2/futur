from __future__ import annotations

from typing import Dict, Mapping, Sequence

import numpy as np

from .schema import DerivativeEvent, OptionQuote


def liquidation_flow(events: Sequence[DerivativeEvent]) -> Dict[str, float]:
    liqs = [e for e in events if e.kind == "liquidation"]
    long_usd = sum(abs(e.value) for e in liqs if e.side in {"long", "sell"})
    short_usd = sum(abs(e.value) for e in liqs if e.side in {"short", "buy"})
    total = long_usd + short_usd
    return {
        "liq_long_usd": float(long_usd),
        "liq_short_usd": float(short_usd),
        "liq_total_usd": float(total),
        "liq_imbalance": float((short_usd - long_usd) / total) if total > 0 else 0.0,
    }


def cascade_pressure(liquidatable_notional_by_bps: Mapping[float, float], absorbable_depth_by_bps: Mapping[float, float]) -> Dict[str, float]:
    bands = sorted(set(liquidatable_notional_by_bps) & set(absorbable_depth_by_bps))
    out = {}
    risks = []
    for band in bands:
        liq = max(float(liquidatable_notional_by_bps[band]), 0.0)
        depth = max(float(absorbable_depth_by_bps[band]), 0.0)
        risk = liq / max(depth, 1.0)
        out["cascade_risk_%gbps" % band] = float(risk)
        risks.append(risk)
    out["cascade_risk_max"] = float(max(risks)) if risks else float("nan")
    out["cascade_risk_mean"] = float(np.mean(risks)) if risks else float("nan")
    return out


def derivatives_state(open_interest: float, previous_open_interest: float, mark: float, index: float, funding: float, premium: float) -> Dict[str, float]:
    oi_delta = (open_interest / previous_open_interest - 1.0) if previous_open_interest > 0 else float("nan")
    basis_bps = 1e4 * (mark - index) / index if index > 0 else float("nan")
    return {
        "open_interest": float(open_interest),
        "oi_delta": float(oi_delta),
        "mark_index_basis_bps": float(basis_bps),
        "funding_rate": float(funding),
        "premium": float(premium),
    }


def _nearest(quotes: Sequence[OptionQuote], option_type: str, target_abs_delta: float) -> OptionQuote:
    subset = [q for q in quotes if q.option_type == option_type]
    if not subset:
        raise ValueError("missing option type")
    target = target_abs_delta if option_type == "call" else -target_abs_delta
    return min(subset, key=lambda q: abs(q.delta - target))


def option_surface_state(quotes: Sequence[OptionQuote], spot: float) -> Dict[str, float]:
    if not quotes:
        return {}
    if spot <= 0:
        raise ValueError("spot must be positive")
    expiries = sorted(set(q.expiry_ts_ns for q in quotes))
    near_expiry = expiries[0]
    near = [q for q in quotes if q.expiry_ts_ns == near_expiry]
    atm = min(near, key=lambda q: abs(q.strike - spot))
    c25 = _nearest(near, "call", 0.25)
    p25 = _nearest(near, "put", 0.25)
    rr25 = c25.mid_iv - p25.mid_iv
    ivs_by_expiry = {}
    for expiry in expiries:
        bucket = [q for q in quotes if q.expiry_ts_ns == expiry]
        atm_q = min(bucket, key=lambda q: abs(q.strike - spot))
        ivs_by_expiry[expiry] = atm_q.mid_iv
    term = ivs_by_expiry[expiries[-1]] - ivs_by_expiry[expiries[0]] if len(expiries) > 1 else 0.0
    return {
        "atm_iv_near": float(atm.mid_iv),
        "rr25_near": float(rr25),
        "atm_iv_term_slope": float(term),
        "option_open_interest": float(sum(max(q.open_interest, 0.0) for q in quotes)),
        "option_volume": float(sum(max(q.volume, 0.0) for q in quotes)),
    }
