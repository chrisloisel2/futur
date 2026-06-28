"""
frontend_pipeline/portfolio_ops_api.py
─────────────────────────────────────────────────────────────────────────────
Endpoints d'observabilité PORTEFEUILLE (cf. brief Étape 14).

Lit des DONNÉES RÉELLES (Decision Ledger, status registry, rapport backtest) —
jamais de mock. Si une source est absente → {"status": "disabled"} (règle projet).

À inclure dans api_server_paper.py :
    from frontend_pipeline.portfolio_ops_api import router as portfolio_ops_router
    app.include_router(portfolio_ops_router)

Endpoints :
    GET /api/portfolio/ledger/summary   A/B/C, shadow PnL, near-miss PnL
    GET /api/portfolio/engines          λ par moteur, A/B/C, PnL, statut
    GET /api/portfolio/regimes          A/B/C par régime
    GET /api/portfolio/validation       évaluation bayésienne par moteur
    GET /api/portfolio/backtest         dernier rapport portfolio walk-forward
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

router = APIRouter(prefix="/api/portfolio", tags=["portfolio-ops"])

_STATUS_REGISTRY = ROOT / "artifacts" / "institutional" / "engines" / "status_registry.json"
_WF_REPORT = ROOT / "artifacts" / "institutional" / "backtests" / "portfolio" / "wf_report.json"


def _ledger():
    from src.institutional.monitoring.decision_ledger import DecisionLedger
    return DecisionLedger()


def _months(ts) -> float:
    span = (ts.max() - ts.min())
    return max(span.days / 30.0, 1e-9)


@router.get("/ledger/summary")
def ledger_summary():
    led = _ledger()
    df = led.load()
    if df.empty:
        return {"status": "disabled", "reason": "ledger vide"}
    return {"status": "ok", **led.summary()}


@router.get("/engines")
def engines():
    df = _ledger().load()
    if df.empty:
        return {"status": "disabled"}
    reg = json.loads(_STATUS_REGISTRY.read_text()) if _STATUS_REGISTRY.exists() else {}
    out = []
    for eng, g in df.groupby("engine_id"):
        m = _months(g["timestamp"])
        a = g[g.decision_zone == "A_TRADE"]
        b = g[g.decision_zone == "B_SHADOW"]
        c = g[g.decision_zone == "C_REJECT"]
        out.append({
            "engine_id": eng,
            "status": reg.get(eng, {}).get("status", "SHADOW"),
            "lambda_a_per_month": round(len(a) / m, 2),
            "n_a": int(len(a)), "n_b": int(len(b)), "n_c": int(len(c)),
            "a_pnl_mean": _safe(a["realized_shadow_result"].mean()),
            "shadow_pnl_mean": _safe(b["realized_shadow_result"].mean()),
            "a_pnl_sum": _safe(a["realized_shadow_result"].sum()),
        })
    return {"status": "ok", "engines": sorted(out, key=lambda x: -x["lambda_a_per_month"])}


@router.get("/regimes")
def regimes():
    df = _ledger().load()
    if df.empty:
        return {"status": "disabled"}
    out = []
    for reg, g in df.groupby("regime"):
        a = int((g.decision_zone == "A_TRADE").sum())
        out.append({
            "regime": str(reg), "n": int(len(g)), "n_a": a,
            "a_rate": round(a / max(len(g), 1), 4),
            "n_b": int((g.decision_zone == "B_SHADOW").sum()),
            "n_c": int((g.decision_zone == "C_REJECT").sum()),
        })
    return {"status": "ok", "regimes": out}


@router.get("/validation")
def validation():
    if not _STATUS_REGISTRY.exists():
        return {"status": "disabled", "reason": "lancer promote_engine.py --all --apply"}
    return {"status": "ok", "registry": json.loads(_STATUS_REGISTRY.read_text())}


@router.get("/backtest")
def backtest():
    if not _WF_REPORT.exists():
        return {"status": "disabled", "reason": "lancer run_portfolio_walk_forward.py"}
    return {"status": "ok", "report": json.loads(_WF_REPORT.read_text())}


def _safe(v):
    try:
        f = float(v)
        return round(f, 6) if f == f else None  # NaN → None
    except Exception:
        return None
