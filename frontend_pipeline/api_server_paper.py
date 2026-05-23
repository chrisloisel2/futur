#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend_pipeline/api_server_paper.py — API Paper Trading (sans MongoDB)
=========================================================================

Remplace api_server.py pour le paper trading.
Lit directement les fichiers CSV/JSON produits par paper_long_signal.py.
Pas de MongoDB, pas de dépendances externes.

Endpoints :
  GET  /health
  GET  /api/market              — prix BTC live + régime
  GET  /api/signal/latest       — dernier signal loggué
  GET  /api/signal/history      — 48h de signaux
  GET  /api/trades              — journal des trades
  GET  /api/state               — gates + métriques PF/WR/DD
  POST /api/data/update         — lance live_data_update.py
  POST /api/signal/run          — lance paper_long_signal.py
  GET  /api/signal/run-status   — est-ce qu'un run est en cours ?

Usage :
  uvicorn frontend_pipeline.api_server_paper:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ─── Chemins ──────────────────────────────────────────────────────────────────

ROOT        = Path(__file__).resolve().parent.parent
REPORT_DIR  = ROOT / "reports" / "paper_trading"
SIGNAL_LOG  = REPORT_DIR / "paper_long_signals.csv"
TRADE_LOG   = REPORT_DIR / "paper_long_trades.csv"
STATE_FILE  = REPORT_DIR / "paper_long_state_v2.json"

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Alpha Paper Trading API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Run state (subprocess tracker) ──────────────────────────────────────────

_run_lock   = threading.Lock()
_run_active = False
_run_log: List[str] = []
_data_update_active = False


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _load_signal_log(n: int = 48) -> List[dict]:
    if not SIGNAL_LOG.exists():
        return []
    try:
        df = pd.read_csv(SIGNAL_LOG).tail(n)
        return df.fillna("").to_dict("records")
    except Exception:
        return []


def _load_trades() -> List[dict]:
    if not TRADE_LOG.exists():
        return []
    try:
        df = pd.read_csv(TRADE_LOG)
        return df.fillna("").to_dict("records")
    except Exception:
        return []


def _fetch_btc_market() -> dict:
    """Fetch BTC 1h price + regime depuis Binance public API."""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": 250},
            timeout=8,
        )
        rows = r.json()
        closes = [float(row[4]) for row in rows]
        price  = closes[-1]
        ema200 = _ema(closes, 200)
        ema50  = _ema(closes, 50)
        vs_ema200 = (price / ema200 - 1.0) * 100
        vs_ema50  = (price / ema50  - 1.0) * 100
        ret_7d  = (closes[-1] / closes[-min(168, len(closes)-1)] - 1.0) * 100
        ret_24h = (closes[-1] / closes[-min(24,  len(closes)-1)] - 1.0) * 100
        regime  = "BULL" if vs_ema200 > 0 else "BEAR"
        return {
            "price":       round(price, 2),
            "vs_ema200":   round(vs_ema200, 2),
            "vs_ema50":    round(vs_ema50, 2),
            "ret_7d":      round(ret_7d, 2),
            "ret_24h":     round(ret_24h, 2),
            "regime":      regime,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "price": None, "regime": "UNKNOWN"}


def _ema(values: List[float], span: int) -> float:
    alpha = 2 / (span + 1)
    v = values[0]
    for x in values[1:]:
        v = alpha * x + (1 - alpha) * v
    return v


def _compute_gates(state: dict) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = state.get("start_date")
    dur_days = 0
    if start:
        try:
            st = pd.Timestamp(start)
            if st.tzinfo is None:
                st = st.tz_localize("UTC")
            dur_days = max(0, (now - st.to_pydatetime()).days)
        except Exception:
            pass

    n_trades = state.get("total_trades", 0)
    n_wins   = state.get("total_wins", 0)
    gw       = state.get("gross_win_sum", 0.0)
    gl       = state.get("gross_loss_sum", 0.0)
    dd       = state.get("max_dd_pct", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= 30 else None

    gates = [
        {"name": "Durée ≥ 90j",    "ok": dur_days >= 90,  "value": dur_days,      "target": 90,   "unit": "j"},
        {"name": "Trades ≥ 100",   "ok": n_trades >= 100, "value": n_trades,      "target": 100,  "unit": ""},
        {"name": "PF live ≥ 1.30", "ok": pf is not None and pf >= 1.30,
         "value": pf,              "target": 1.30, "unit": ""},
        {"name": "DD < 3%",        "ok": dd < 3.0,        "value": round(dd, 2),  "target": 3.0,  "unit": "%"},
        {"name": "Erreurs",        "ok": state.get("accounting_errors", 0) == 0,
         "value": state.get("accounting_errors", 0), "target": 0, "unit": ""},
        {"name": "Drift",          "ok": state.get("drift_alerts", 0) == 0,
         "value": state.get("drift_alerts", 0),      "target": 0, "unit": ""},
    ]
    return gates


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":    "ok",
        "service":   "paper-trading-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/market")
def market():
    return _fetch_btc_market()


@app.get("/api/signal/latest")
def signal_latest():
    rows = _load_signal_log(1)
    if not rows:
        return {"action": "NO_DATA", "timestamp": None}
    return rows[0]


@app.get("/api/signal/history")
def signal_history(n: int = 48):
    return _load_signal_log(n)


@app.get("/api/trades")
def trades():
    return _load_trades()


@app.get("/api/state")
def state():
    s     = _load_state()
    gates = _compute_gates(s)

    n_trades = s.get("total_trades", 0)
    n_wins   = s.get("total_wins", 0)
    gw       = s.get("gross_win_sum", 0.0)
    gl       = s.get("gross_loss_sum", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= 30 else None
    wr       = round(n_wins / max(n_trades, 1), 3) if n_trades > 0 else None

    all_ok = all(g["ok"] for g in gates)

    return {
        "total_trades":       n_trades,
        "total_wins":         n_wins,
        "pf_live":            pf,
        "wr_live":            wr,
        "max_dd_pct":         s.get("max_dd_pct", 0.0),
        "cumulative_pnl_pct": s.get("cumulative_pnl_pct", 0.0),
        "consecutive_losses": s.get("consecutive_losses", 0),
        "weekly_pnl_pct":     s.get("weekly_pnl_pct", 0.0),
        "crash_halt_until":   s.get("crash_halt_until"),
        "start_date":         s.get("start_date"),
        "drift_alerts":       s.get("drift_alerts", 0),
        "accounting_errors":  s.get("accounting_errors", 0),
        "gates":              gates,
        "all_gates_ok":       all_ok,
        "verdict":            "LIVE_CANDIDATE" if all_ok else "PAPER_ONLY",
    }


@app.get("/api/signal/run-status")
def run_status():
    return {
        "signal_running":      _run_active,
        "data_update_running": _data_update_active,
        "log":                 _run_log[-10:],
    }


@app.post("/api/data/update")
def data_update(symbols: str = "BTCUSDT,ETHUSDT"):
    global _data_update_active, _run_log

    if _data_update_active:
        return {"status": "already_running"}

    sym_list = symbols.split(",")

    def _run():
        global _data_update_active, _run_log
        _data_update_active = True
        _run_log = ["[data_update] Démarrage…"]
        cmd = ["python3", str(ROOT / "scripts" / "live_data_update.py"),
               "--symbols"] + sym_list
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  cwd=str(ROOT), timeout=300)
            _run_log.extend(proc.stdout.splitlines()[-20:])
            if proc.returncode != 0:
                _run_log.append(f"[ERROR] {proc.stderr[-500:]}")
        except Exception as e:
            _run_log.append(f"[ERROR] {e}")
        finally:
            _data_update_active = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.post("/api/signal/run")
def signal_run(update_data: bool = True):
    global _run_active, _run_log

    if _run_active:
        return {"status": "already_running"}

    def _run():
        global _run_active, _run_log
        _run_active = True
        _run_log = ["[signal_run] Démarrage…"]

        # 1. Mise à jour des données
        if update_data:
            _run_log.append("[signal_run] Mise à jour données live…")
            data_cmd = ["python3", str(ROOT / "scripts" / "live_data_update.py"),
                        "--symbols", "BTCUSDT", "ETHUSDT"]
            try:
                p = subprocess.run(data_cmd, capture_output=True, text=True,
                                   cwd=str(ROOT), timeout=300)
                _run_log.extend(p.stdout.splitlines()[-10:])
            except Exception as e:
                _run_log.append(f"[WARN data] {e}")

        # 2. Signal paper
        _run_log.append("[signal_run] Génération signal…")
        sig_cmd = ["python3", str(ROOT / "scripts" / "paper_long_signal.py")]
        try:
            p = subprocess.run(sig_cmd, capture_output=True, text=True,
                               cwd=str(ROOT), timeout=600)
            _run_log.extend(p.stdout.splitlines()[-30:])
            if p.returncode != 0:
                _run_log.append(f"[ERROR] {p.stderr[-500:]}")
        except Exception as e:
            _run_log.append(f"[ERROR signal] {e}")
        finally:
            _run_active = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
