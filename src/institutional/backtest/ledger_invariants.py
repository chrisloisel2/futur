"""
src/institutional/backtest/ledger_invariants.py
─────────────────────────────────────────────────────────────────────────────
Phase 4E commit 12 -- independent cross-ledger validator for
`MultiLegResult`.

"Independent" means this module never calls back into `MultiLegBacktester`
and never trusts its internal running accumulators (`cash`, `pnl_acc`) --
it reads only the two PUBLIC output tables (`leg_ledger`,
`portfolio_ledger`) and `config.initial_capital`, and recomputes every
figure it checks from those alone. This is deliberate: Phase 4D commit 9's
bug was a disagreement between `portfolio_ledger` and `leg_ledger` that
each ledger's own construction code could not have caught (each side was
internally self-consistent; only comparing the two together exposed it).
A validator built out of the same internals would share the same blind
spot.

No tolerance here may be widened to hide a structural disagreement --
`_TOL` is sized to absorb ordinary float64 summation noise (order 1e-9 to
1e-6 relative to a $10k-$1M-scale backtest) and nothing else. Phase 4D
commit 9's actual bug (portfolio_ledger showing `gross_exposure=3.11`
against leg_ledger's `0`) was ~9 orders of magnitude larger than that --
any real recurrence of it, or a differently-shaped terminal-ledger bug,
fails loudly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from src.institutional.portfolio.position import LEG_DELTA_SIGN

if TYPE_CHECKING:
    from src.institutional.backtest.multileg_backtester import MultiLegResult

_TOL = 1e-6


class LedgerInconsistencyError(Exception):
    """Raised when portfolio_ledger and leg_ledger disagree about
    something a correct backtest run can never disagree about. Never
    caught internally and silenced -- a caller that wants to tolerate a
    specific, understood exception must do so explicitly, field by
    field, not by catching this type broadly."""


@dataclass(frozen=True)
class LedgerCoherenceReport:
    """What the validator actually found, for callers that want to log
    or assert on specifics rather than only catch-or-not. `ok` is True
    iff every check passed; `violations` is empty in that case."""
    ok: bool
    violations: list[str]
    terminal_timestamp: object
    recomputed_gross_exposure: float
    recomputed_net_exposure: float
    recomputed_cash: float
    reported_cash: float


def _is_open(row: pd.Series) -> bool:
    return pd.isna(row["exit_time"])


def _terminal_price(row: pd.Series) -> float:
    """The price a still-open leg's exposure would be marked at, or the
    real exit price for a closed one. `MultiLegBacktester.run()`
    force-closes every leg by its own `end` (Phase 4D commit 8's
    finding, documented in truth_shadow/mapping.py's module docstring),
    so `_is_open` should never be True here in practice -- this exists
    so the check below fails loudly on a real value instead of raising
    a KeyError if that invariant is ever broken."""
    return float(row["entry_price"]) if _is_open(row) else float(row["exit_price"])


def validate_terminal_ledger_coherence(result: MultiLegResult) -> LedgerCoherenceReport:
    """The independent check. Reads `result.leg_ledger`,
    `result.portfolio_ledger`, `result.config.initial_capital` only --
    recomputes everything else. Returns a report; call
    `raise_if_inconsistent(result)` (below) to turn it into an exception."""
    violations: list[str] = []
    leg_ledger = result.leg_ledger
    portfolio_ledger = result.portfolio_ledger

    if portfolio_ledger.empty:
        violations.append("portfolio_ledger is empty -- no terminal row to validate against")
        return LedgerCoherenceReport(False, violations, None, 0.0, 0.0, 0.0, 0.0)

    terminal_row = portfolio_ledger.iloc[-1]
    terminal_ts = terminal_row["timestamp"]

    # ── aucun événement postérieur au dernier snapshot portefeuille ────────
    if not leg_ledger.empty:
        entry_ts = pd.to_datetime(leg_ledger["entry_time"], utc=True)
        exit_ts = pd.to_datetime(leg_ledger["exit_time"], utc=True)
        terminal_ts_parsed = pd.Timestamp(terminal_ts)
        if (entry_ts > terminal_ts_parsed).any():
            bad = leg_ledger.loc[entry_ts > terminal_ts_parsed, "leg_id"].tolist()
            violations.append(
                f"leg_ledger has entry_time after the terminal portfolio_ledger "
                f"timestamp ({terminal_ts}): legs {bad}")
        if (exit_ts.dropna() > terminal_ts_parsed).any():
            bad = leg_ledger.loc[exit_ts > terminal_ts_parsed, "leg_id"].tolist()
            violations.append(
                f"leg_ledger has exit_time after the terminal portfolio_ledger "
                f"timestamp ({terminal_ts}): legs {bad}")

    # ── position ouverte/fermée cohérente : toutes les jambes d'une même
    # position doivent être toutes ouvertes ou toutes fermées ──────────────
    if not leg_ledger.empty:
        for position_id, group in leg_ledger.groupby("position_id"):
            open_flags = group["exit_time"].apply(lambda v: pd.isna(v))
            if open_flags.any() and not open_flags.all():
                violations.append(
                    f"position {position_id!r} has a mix of open and closed legs "
                    f"at the terminal snapshot: {group[['leg_id', 'exit_time']].to_dict('records')}")

    # ── clôtures terminales toutes intégrées : toute jambe fermée exactement
    # au timestamp terminal doit avoir un exit_price/costs/net_pnl calculés
    # (pas de clôture résiduelle partiellement appliquée) ───────────────────
    if not leg_ledger.empty:
        terminal_closes = leg_ledger[pd.to_datetime(leg_ledger["exit_time"], utc=True, errors="coerce")
                                     == pd.Timestamp(terminal_ts)]
        for col in ("exit_price", "costs", "net_pnl"):
            missing = terminal_closes[terminal_closes[col].isna()]
            if not missing.empty:
                violations.append(
                    f"{len(missing)} terminal-close leg(s) have missing {col!r}: "
                    f"{missing['leg_id'].tolist()}")

    # ── quantités terminales identiques + exposition brute/nette cohérente ──
    # recalculées indépendamment depuis leg_ledger (jambes encore ouvertes au
    # snapshot terminal, i.e. exit_time manquant -- structurellement vide vu
    # le force-close de run(), mais vérifié en toute généralité) ────────────
    gross = 0.0
    net = 0.0
    if not leg_ledger.empty:
        for _, row in leg_ledger.iterrows():
            if not _is_open(row):
                continue
            price = _terminal_price(row)
            qty = float(row["qty"])
            sign = LEG_DELTA_SIGN[row["leg_type"]]
            gross += qty * price
            net += sign * qty * price
    equity_for_norm = float(terminal_row["equity"]) or 1.0
    recomputed_gross = gross / equity_for_norm
    recomputed_net = net / equity_for_norm

    for field, recomputed in (("gross_exposure", recomputed_gross), ("net_exposure", recomputed_net)):
        if field not in terminal_row.index:
            continue
        reported = float(terminal_row[field])
        if abs(reported - recomputed) > _TOL:
            violations.append(
                f"{field}: portfolio_ledger's terminal row reports {reported!r}, but "
                f"recomputing independently from leg_ledger's own open/closed state "
                f"gives {recomputed!r} (|diff|={abs(reported - recomputed)!r} > {_TOL})")

    # ── aucun coût terminal omis ou déduit deux fois ────────────────────────
    # cash terminal recalculé uniquement depuis PositionLeg.net_pnl() (déjà
    # présent, non modifié, dans leg_ledger) + le seul bucket jamais suivi
    # par jambe (borrow, lu depuis la colonne du ledger, pas depuis pnl_acc).
    initial_capital = float(result.config.initial_capital)
    realized_total = float(leg_ledger["net_pnl"].sum()) if not leg_ledger.empty else 0.0
    borrow_total = float(terminal_row.get("borrow_total", 0.0))
    recomputed_cash = initial_capital + realized_total + borrow_total
    reported_cash = float(terminal_row["cash"])
    if abs(reported_cash - recomputed_cash) > _TOL * max(1.0, abs(initial_capital)):
        violations.append(
            f"cash: portfolio_ledger's terminal row reports {reported_cash!r}, but "
            f"initial_capital + sum(leg_ledger.net_pnl) + borrow_total gives "
            f"{recomputed_cash!r} (|diff|={abs(reported_cash - recomputed_cash)!r}) -- "
            f"a terminal cost was omitted from, or double-counted into, one of the "
            f"two ledgers")

    return LedgerCoherenceReport(
        ok=not violations, violations=violations, terminal_timestamp=terminal_ts,
        recomputed_gross_exposure=recomputed_gross, recomputed_net_exposure=recomputed_net,
        recomputed_cash=recomputed_cash, reported_cash=reported_cash,
    )


def raise_if_inconsistent(result: MultiLegResult) -> LedgerCoherenceReport:
    """Convenience wrapper: run the validator and raise
    `LedgerInconsistencyError` listing every violation found (not just the
    first) if there are any. Returns the report on success."""
    report = validate_terminal_ledger_coherence(result)
    if not report.ok:
        joined = "\n  - ".join(report.violations)
        raise LedgerInconsistencyError(
            f"terminal ledger coherence check failed ({len(report.violations)} "
            f"violation(s)):\n  - {joined}")
    return report
