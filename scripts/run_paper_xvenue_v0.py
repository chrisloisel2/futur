#!/usr/bin/env python3
"""
scripts/run_paper_xvenue_v0.py
─────────────────────────────────────────────────────────────────────────────
PAPER-LIVE FUNDING_XVENUE v0 — sleeve SÉPARÉ (décision humaine 2026-07-19).

Phase paper forward prévue au §8 du protocole gelé (reports/
FUNDING_XVENUE_PROTOCOL.md), démarrée AVANT l'exécution du test one-shot
historique sur décision humaine explicite. Discipline conservée :

  • la règle jugée est IMPORTÉE du script gelé scripts/test_funding_xvenue_v0.py
    (run_rule + PARAMS) — aucune réimplémentation, aucun re-tuning ;
  • le paper 200k (Portfolio V1.1) n'est PAS touché : capital, ledgers et
    state.json propres sous reports/paper_live/xvenue/ ;
  • le test one-shot reste à exécuter tel quel ; ce sleeve n'en lit ni n'en
    écrit les données (stores live séparés sous data/paper_xvenue/) ;
  • PnL comptabilisé uniquement à partir de --paper-start (position héritée du
    warm-up facturée à l'entrée : side_cost au premier settlement compté).

Données live (API publiques, aucun secret) :
  GET  https://fapi.binance.com/fapi/v1/fundingRate     funding réalisé 8 h
  POST https://api.hyperliquid.xyz/info fundingHistory  funding réalisé horaire

Stores append-only idempotents (dédup timestamp) depuis EPOCH (2026-07-05,
fixe → recompute déterministe du chemin d'hystérèse) :
  data/paper_xvenue/binance/{SYM}.parquet
  data/paper_xvenue/hyperliquid/{COIN}.parquet

Sorties (recompute déterministe à chaque cycle, style run_paper_portfolio_v1) :
  reports/paper_live/xvenue/ledger.parquet   1 ligne / settlement / coin
  reports/paper_live/xvenue/state.json

Cap exchange-risk Hyperliquid pré-déclaré ≤ 25 % du capital du moteur : suivi
dans state.json (paper : notional jambe HL / capital sleeve).
Service : deploy/systemd/futur-paper-xvenue.{service,timer} (horaire).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_funding_xvenue_v0 import ANN, PARAMS, run_rule  # noqa: E402  règle GELÉE

STORE = ROOT / "data" / "paper_xvenue"
OUT = ROOT / "reports" / "paper_live" / "xvenue"
EPOCH = pd.Timestamp("2026-07-05T00:00:00Z")   # ancre fixe du warm-up (≥ 21 settlements avant paper_start)
COINS = PARAMS["coins"]                         # {BTC: BTCUSDT, ...} — figé
RT_HL_BP = (2 * (PARAMS["fee_binance_bp"] + PARAMS["slippage_bp"])
            + 2 * (PARAMS["fee_hl_bp"] + PARAMS["slippage_bp"])
            + PARAMS["basis_rt_bp"])            # 31 bp ×1, identique au test
HL_EXCH_CAP = 0.25                              # cap HL pré-déclaré (§8)

BINANCE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
HL_URL = "https://api.hyperliquid.xyz/info"


def _read_store(path: Path) -> pd.Series:
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(path)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    return pd.Series(df["funding_rate"].values, index=ts).sort_index()


def _write_store(path: Path, ser: pd.Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ser = ser[~ser.index.duplicated(keep="last")].sort_index()
    pd.DataFrame({"timestamp": ser.index, "funding_rate": ser.values}
                 ).to_parquet(path, index=False)


def fetch_binance(sym: str, since: pd.Timestamp) -> pd.Series:
    rows, start = [], int(since.timestamp() * 1000)
    for _ in range(10):
        r = requests.get(BINANCE_URL, params={"symbol": sym, "startTime": start,
                                              "limit": 1000}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start = batch[-1]["fundingTime"] + 1
    if not rows:
        return pd.Series(dtype=float)
    ts = pd.to_datetime([b["fundingTime"] for b in rows], unit="ms", utc=True)
    return pd.Series([float(b["fundingRate"]) for b in rows], index=ts)


def fetch_hyperliquid(coin: str, since: pd.Timestamp) -> pd.Series:
    rows, start = [], int(since.timestamp() * 1000)
    for _ in range(40):
        r = requests.post(HL_URL, json={"type": "fundingHistory", "coin": coin,
                                        "startTime": start}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 500:
            break
        start = int(batch[-1]["time"]) + 1
        time.sleep(0.2)
    if not rows:
        return pd.Series(dtype=float)
    ts = pd.to_datetime([int(b["time"]) for b in rows], unit="ms", utc=True)
    return pd.Series([float(b["fundingRate"]) for b in rows], index=ts)


def update_stores() -> dict:
    """Top-up incrémental depuis EPOCH ; retourne la fraîcheur par venue/coin."""
    fresh = {}
    for coin, sym in COINS.items():
        for venue, fetch, key in (("binance", fetch_binance, sym),
                                  ("hyperliquid", fetch_hyperliquid, coin)):
            path = STORE / venue / f"{key}.parquet"
            cur = _read_store(path)
            since = cur.index.max() + pd.Timedelta("1ms") if len(cur) else EPOCH
            try:
                new = fetch(key, since)
            except Exception as e:  # noqa: BLE001 — un cycle raté ≠ store corrompu
                print(f"[xvenue] top-up {venue}/{key} échec: {e}", flush=True)
                new = pd.Series(dtype=float)
            if len(new):
                cur = pd.concat([cur, new])
                _write_store(path, cur)
            fresh[f"{venue}_{coin}"] = str(cur.index.max()) if len(cur) else None
    return fresh


def build_differential_live(coin: str, sym: str) -> pd.Series:
    """d_t en bp/8h — même math que build_differential gelé, sur les stores live."""
    bn = _read_store(STORE / "binance" / f"{sym}.parquet")
    hl = _read_store(STORE / "hyperliquid" / f"{coin}.parquet")
    if len(bn) < 3 or len(hl) < 10:
        return pd.Series(dtype=float)
    bn.index = bn.index.round("1h")
    bn = bn[bn.index >= hl.index.min()]
    cum = hl.cumsum()
    pos = cum.index.searchsorted(bn.index, side="right") - 1
    vals = np.where(pos >= 0, cum.values[np.maximum(pos, 0)], 0.0)
    win = np.diff(vals, prepend=0.0)
    d = pd.Series(win[1:] - bn.values[1:], index=bn.index[1:]) * 1e4
    gaps = d.index.to_series().diff().dt.total_seconds().div(3600).dropna()
    if len(gaps) >= 10:
        assert gaps.median() == 8.0, f"{coin}: cadence médiane {gaps.median()} != 8h"
    return d[~d.isna()]


def account_from_start(res: dict, start: pd.Timestamp, cost_mult: float) -> pd.Series:
    """Net (bp de N) comptabilisé après start ; position héritée facturée à l'entrée."""
    net = res["net"][res["net"].index > start].copy()
    if len(net) and res["held"].reindex(net.index).iloc[0] != 0:
        # position héritée du warm-up : le book paper l'ouvre au start → coût
        # d'entrée facturé ici (l'entrée réelle, antérieure, n'est pas comptée)
        net.iloc[0] -= RT_HL_BP / 2.0 * cost_mult
    return net


def run_once(args) -> dict:
    fresh = update_stores()
    start = pd.Timestamp(args.paper_start, tz="UTC")
    cap_coin = args.capital / len(COINS)        # N par coin (capital requis = N, §3)

    rows, per_coin = [], {}
    tot_x1 = tot_x2 = 0.0
    for coin, sym in COINS.items():
        d = build_differential_live(coin, sym)
        if len(d) <= PARAMS["lookback"]:
            per_coin[coin] = {"status": "warmup", "n_settlements": int(len(d))}
            continue
        r1 = run_rule(d, PARAMS["lookback"], PARAMS["theta_in_ann"],
                      PARAMS["theta_out_ann"], 1.0, RT_HL_BP)
        r2 = run_rule(d, PARAMS["lookback"], PARAMS["theta_in_ann"],
                      PARAMS["theta_out_ann"], 2.0, RT_HL_BP)
        net1, net2 = account_from_start(r1, start, 1.0), account_from_start(r2, start, 2.0)
        s_ann = (d.rolling(PARAMS["lookback"]).mean() * ANN).iloc[-1]
        acc = r1["held"].reindex(net1.index)
        for t in net1.index:
            rows.append({"settlement": t, "coin": coin, "d_bp": float(d.loc[t]),
                         "held": int(acc.loc[t]), "net_bp_x1": float(net1.loc[t]),
                         "net_bp_x2": float(net2.loc[t])})
        years = max(len(net1) * 8.0 / (24 * 365), 1e-9)
        per_coin[coin] = {
            "status": "active", "n_settlements": int(len(d)),
            "accounted": int(len(net1)),
            "S_ann_now": round(float(s_ann), 3),
            "pos_now": int(r1["pos"].iloc[-1]),
            "net_bp_cum_x1": round(float(net1.sum()), 2),
            "net_bp_cum_x2": round(float(net2.sum()), 2),
            "net_ann_x1": round(float(net1.sum()) / 100.0 / years, 2),
            "rt_total": int(r1["rt"]), "time_in_pos": round(r1["time_in_pos"], 3),
        }
        tot_x1 += net1.sum() / 1e4 * cap_coin
        tot_x2 += net2.sum() / 1e4 * cap_coin

    OUT.mkdir(parents=True, exist_ok=True)
    if rows:
        pd.DataFrame(rows).sort_values(["settlement", "coin"]).to_parquet(
            OUT / "ledger.parquet", index=False)
    n_open = sum(1 for v in per_coin.values() if v.get("pos_now"))
    hl_leg_frac = n_open * cap_coin / args.capital if args.capital else 0.0
    state = {
        "sleeve": "FUNDING_XVENUE_PAPER_V0",
        "protocol": "reports/FUNDING_XVENUE_PROTOCOL.md",
        "note": ("Sleeve paper SÉPARÉ démarré avant le test one-shot sur décision "
                 "humaine 2026-07-19 ; règle et coûts importés du script gelé, "
                 "paper 200k intouché."),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "paper_start": args.paper_start, "epoch": str(EPOCH.date()),
        "capital": args.capital, "notional_per_coin": cap_coin,
        "equity_x1": round(args.capital + tot_x1, 2),
        "equity_x2": round(args.capital + tot_x2, 2),
        "hl_leg_fraction": round(hl_leg_frac, 3),
        "hl_cap_predeclared": HL_EXCH_CAP,
        "hl_cap_breached": bool(hl_leg_frac > HL_EXCH_CAP),
        "per_coin": per_coin, "data_freshness": fresh,
    }
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
    print(f"[paper xvenue] equity ×1 {state['equity_x1']:.0f} "
          f"×2 {state['equity_x2']:.0f}  "
          + "  ".join(f"{c}:{v.get('pos_now', 'warmup')}" for c, v in per_coin.items()),
          flush=True)
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=30000)
    ap.add_argument("--paper-start", default="2026-07-19")
    ap.add_argument("--loop-interval", type=int, default=0)
    args = ap.parse_args()
    while True:
        try:
            run_once(args)
        except Exception as e:  # noqa: BLE001
            print(f"[paper xvenue] cycle échec: {e}", flush=True)
        if args.loop_interval <= 0:
            break
        time.sleep(args.loop_interval)


if __name__ == "__main__":
    main()
