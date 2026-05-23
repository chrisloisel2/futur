#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend_pipeline/api_server_paper.py — API Paper Trading Multi-Asset (sans MongoDB)
======================================================================================

Endpoints BTC single-asset (paper_long_signal.py) :
  GET  /health
  GET  /api/market
  GET  /api/signal/latest
  GET  /api/signal/history
  GET  /api/trades
  GET  /api/state
  POST /api/data/update
  POST /api/signal/run
  GET  /api/signal/run-status

Endpoints Fleet multi-asset (paper_multi_signal.py) :
  GET  /api/fleet                  — résumé global + tous les assets
  GET  /api/fleet/{symbol}         — détail d'un asset
  GET  /api/fleet/{symbol}/signals — historique signaux
  GET  /api/fleet/{symbol}/trades  — trades de l'asset
  POST /api/fleet/run              — lance paper_multi_signal.py
  GET  /api/fleet/run-status       — statut du run fleet

Usage :
  uvicorn frontend_pipeline.api_server_paper:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ─── Chemins ──────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).resolve().parent.parent
REPORT_DIR    = ROOT / "reports" / "paper_trading"
SIGNAL_LOG    = REPORT_DIR / "paper_long_signals.csv"
TRADE_LOG     = REPORT_DIR / "paper_long_trades.csv"
STATE_FILE    = REPORT_DIR / "paper_long_state_v2.json"
FLEET_SUMMARY = REPORT_DIR / "fleet_summary.json"

ENRICHED_DIR  = ROOT / "data" / "enriched"

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Alpha Paper Trading API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Run state ────────────────────────────────────────────────────────────────

_run_lock          = threading.Lock()
_run_active        = False
_run_log: List[str] = []
_data_update_active = False
_fleet_run_active  = False
_fleet_run_log: List[str] = []


# ─── Helpers single-asset (BTC) ───────────────────────────────────────────────

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
        return pd.read_csv(TRADE_LOG).fillna("").to_dict("records")
    except Exception:
        return []


def _fetch_btc_market() -> dict:
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
        ret_7d    = (closes[-1] / closes[-min(168, len(closes)-1)] - 1.0) * 100
        ret_24h   = (closes[-1] / closes[-min(24,  len(closes)-1)] - 1.0) * 100
        regime    = "BULL" if vs_ema200 > 0 else "BEAR"
        return {
            "price":     round(price, 2),
            "vs_ema200": round(vs_ema200, 2),
            "vs_ema50":  round(vs_ema50, 2),
            "ret_7d":    round(ret_7d, 2),
            "ret_24h":   round(ret_24h, 2),
            "regime":    regime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
    gw       = state.get("gross_win_sum", 0.0)
    gl       = state.get("gross_loss_sum", 0.0)
    dd       = state.get("max_dd_pct", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= 30 else None

    return [
        {"name": "Durée ≥ 90j",    "ok": dur_days >= 90,  "value": dur_days,     "target": 90,   "unit": "j"},
        {"name": "Trades ≥ 100",   "ok": n_trades >= 100, "value": n_trades,     "target": 100,  "unit": ""},
        {"name": "PF live ≥ 1.30", "ok": pf is not None and pf >= 1.30,
         "value": pf,              "target": 1.30, "unit": ""},
        {"name": "DD < 3%",        "ok": dd < 3.0,        "value": round(dd, 2), "target": 3.0,  "unit": "%"},
        {"name": "Erreurs",        "ok": state.get("accounting_errors", 0) == 0,
         "value": state.get("accounting_errors", 0), "target": 0, "unit": ""},
        {"name": "Drift",          "ok": state.get("drift_alerts", 0) == 0,
         "value": state.get("drift_alerts", 0),      "target": 0, "unit": ""},
    ]


# ─── Helpers fleet multi-asset ────────────────────────────────────────────────

def _asset_dir(symbol: str) -> Path:
    return REPORT_DIR / symbol


def _load_fleet_summary() -> dict:
    if FLEET_SUMMARY.exists():
        try:
            return json.loads(FLEET_SUMMARY.read_text())
        except Exception:
            pass
    # Construire un résumé minimal depuis les states per-asset
    assets = []
    for sym in TOP_10:
        state_f = _asset_dir(sym) / "state.json"
        sig_f   = _asset_dir(sym) / "signals.csv"
        available = (ENRICHED_DIR / f"{sym}_1h_enriched.parquet").exists()
        if not available:
            assets.append({"symbol": sym, "available": False, "action": "NO_DATA"})
            continue
        state = {}
        if state_f.exists():
            try:
                state = json.loads(state_f.read_text())
            except Exception:
                pass
        latest_signal = {}
        if sig_f.exists():
            try:
                df = pd.read_csv(sig_f).tail(1)
                if not df.empty:
                    latest_signal = df.fillna("").to_dict("records")[0]
            except Exception:
                pass
        assets.append({
            "symbol":       sym,
            "available":    True,
            "action":       latest_signal.get("action", "PENDING"),
            "p_long":       latest_signal.get("p_long"),
            "timestamp":    latest_signal.get("timestamp"),
            "total_trades": state.get("total_trades", 0),
            "max_dd_pct":   state.get("max_dd_pct", 0.0),
            "cumulative_pnl_pct": state.get("cumulative_pnl_pct", 0.0),
        })
    btc = _fetch_btc_market()
    return {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "btc_regime":  btc.get("regime", "UNKNOWN"),
        "btc_price":   btc.get("price"),
        "btc_vs_ema200": btc.get("vs_ema200"),
        "assets":      assets,
    }


def _load_asset_detail(symbol: str) -> dict:
    state_f   = _asset_dir(symbol) / "state.json"
    sig_f     = _asset_dir(symbol) / "signals.csv"
    trade_f   = _asset_dir(symbol) / "trades.csv"
    available = (ENRICHED_DIR / f"{symbol}_1h_enriched.parquet").exists()

    if not available:
        return {"symbol": symbol, "available": False}

    state = {}
    if state_f.exists():
        try:
            state = json.loads(state_f.read_text())
        except Exception:
            pass

    latest_signal = {}
    if sig_f.exists():
        try:
            rows = pd.read_csv(sig_f).tail(1)
            if not rows.empty:
                latest_signal = rows.fillna("").to_dict("records")[0]
        except Exception:
            pass

    n_trades = state.get("total_trades", 0)
    gw       = state.get("gross_win_sum", 0.0)
    gl       = state.get("gross_loss_sum", 0.0)
    dd       = state.get("max_dd_pct", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= 30 else None
    wr       = round(state.get("total_wins", 0) / max(n_trades, 1), 3) if n_trades > 0 else None

    gates    = _compute_gates(state)
    all_ok   = all(g["ok"] for g in gates)

    return {
        "symbol":              symbol,
        "available":           True,
        "latest_signal":       latest_signal,
        "total_trades":        n_trades,
        "total_wins":          state.get("total_wins", 0),
        "pf_live":             pf,
        "wr_live":             wr,
        "max_dd_pct":          dd,
        "cumulative_pnl_pct":  state.get("cumulative_pnl_pct", 0.0),
        "consecutive_losses":  state.get("consecutive_losses", 0),
        "weekly_pnl_pct":      state.get("weekly_pnl_pct", 0.0),
        "crash_halt_until":    state.get("crash_halt_until"),
        "start_date":          state.get("start_date"),
        "drift_alerts":        state.get("drift_alerts", 0),
        "accounting_errors":   state.get("accounting_errors", 0),
        "gates":               gates,
        "all_gates_ok":        all_ok,
        "verdict":             "LIVE_CANDIDATE" if all_ok else "PAPER_ONLY",
    }


# ─── Single-asset endpoints ───────────────────────────────────────────────────

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
    gw       = s.get("gross_win_sum", 0.0)
    gl       = s.get("gross_loss_sum", 0.0)
    pf       = round(gw / max(gl, 1e-9), 3) if n_trades >= 30 else None
    wr       = round(s.get("total_wins", 0) / max(n_trades, 1), 3) if n_trades > 0 else None
    all_ok   = all(g["ok"] for g in gates)
    return {
        "total_trades":       n_trades,
        "total_wins":         s.get("total_wins", 0),
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
        cmd = ["python3", str(ROOT / "scripts" / "live_data_update.py"), "--symbols"] + sym_list
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=300)
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
        if update_data:
            _run_log.append("[signal_run] Mise à jour données live…")
            try:
                p = subprocess.run(
                    ["python3", str(ROOT / "scripts" / "live_data_update.py"),
                     "--symbols", "BTCUSDT", "ETHUSDT"],
                    capture_output=True, text=True, cwd=str(ROOT), timeout=300,
                )
                _run_log.extend(p.stdout.splitlines()[-10:])
            except Exception as e:
                _run_log.append(f"[WARN data] {e}")
        _run_log.append("[signal_run] Génération signal…")
        try:
            p = subprocess.run(
                ["python3", str(ROOT / "scripts" / "paper_long_signal.py")],
                capture_output=True, text=True, cwd=str(ROOT), timeout=600,
            )
            _run_log.extend(p.stdout.splitlines()[-30:])
            if p.returncode != 0:
                _run_log.append(f"[ERROR] {p.stderr[-500:]}")
        except Exception as e:
            _run_log.append(f"[ERROR signal] {e}")
        finally:
            _run_active = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ─── Fleet multi-asset endpoints ──────────────────────────────────────────────

@app.get("/api/fleet")
def fleet():
    return _load_fleet_summary()


@app.get("/api/fleet/run-status")
def fleet_run_status():
    return {
        "fleet_running": _fleet_run_active,
        "log":           _fleet_run_log[-20:],
    }


@app.get("/api/fleet/{symbol}")
def fleet_asset(symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return _load_asset_detail(symbol)


@app.get("/api/fleet/{symbol}/signals")
def fleet_asset_signals(symbol: str, n: int = 100):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    f = _asset_dir(symbol) / "signals.csv"
    if not f.exists():
        return []
    try:
        return pd.read_csv(f).tail(n).fillna("").to_dict("records")
    except Exception:
        return []


@app.get("/api/fleet/{symbol}/trades")
def fleet_asset_trades(symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    f = _asset_dir(symbol) / "trades.csv"
    if not f.exists():
        return []
    try:
        return pd.read_csv(f).fillna("").to_dict("records")
    except Exception:
        return []


@app.post("/api/fleet/run")
def fleet_run(update_data: bool = True):
    global _fleet_run_active, _fleet_run_log
    if _fleet_run_active:
        return {"status": "already_running"}

    def _run():
        global _fleet_run_active, _fleet_run_log
        _fleet_run_active = True
        _fleet_run_log = ["[fleet_run] Démarrage…"]

        # Update all available enriched data first
        if update_data:
            _fleet_run_log.append("[fleet_run] live_data_update pour tous les assets…")
            available = [s for s in TOP_10
                         if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
            if available:
                try:
                    p = subprocess.run(
                        ["python3", str(ROOT / "scripts" / "live_data_update.py"),
                         "--symbols"] + available,
                        capture_output=True, text=True, cwd=str(ROOT), timeout=600,
                    )
                    _fleet_run_log.extend(p.stdout.splitlines()[-15:])
                except Exception as e:
                    _fleet_run_log.append(f"[WARN data] {e}")

        _fleet_run_log.append("[fleet_run] paper_multi_signal…")
        try:
            p = subprocess.run(
                ["python3", str(ROOT / "scripts" / "paper_multi_signal.py")],
                capture_output=True, text=True, cwd=str(ROOT), timeout=1200,
            )
            _fleet_run_log.extend(p.stdout.splitlines()[-40:])
            if p.returncode != 0:
                _fleet_run_log.append(f"[ERROR] {p.stderr[-500:]}")
        except Exception as e:
            _fleet_run_log.append(f"[ERROR fleet] {e}")
        finally:
            _fleet_run_active = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
