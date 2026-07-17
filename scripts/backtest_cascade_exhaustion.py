#!/usr/bin/env python3
"""
scripts/backtest_cascade_exhaustion.py
─────────────────────────────────────────────────────────────────────────────
ÉPUISEMENT DE CASCADE — expérience séparée (piste #2 Edge Factory),
liquidations RÉELLES cm (Vision liquidationSnapshot, 2023-06-25→2026-01-01).

Protocole PRÉ-ENREGISTRÉ (un run, verdict sur le primaire) :

  Setup (cascade récente) puis déclencheur (retournement) à l'heure t,
  tout causal (info ≤ close t) :
    1. cascade vendeuse extrême RÉCENTE : max du notional long-liq 1 h
       (cm PERP du même coin, side=SELL, contrats×taille) sur [t−3, t]
       ≥ P99 glissant 90 j (> 0) ;
    2. chute OI : oi(t)/oi(t−4h) − 1 ≤ −2 % (OI 5-min Vision um → 1 h) ;
    3. funding normalisé : z-score 30 j du funding (8 h ffillé 1 h) ≤ 0,5 ;
    4. absorption : close_position_in_range(t) ≥ 0,5 (close moitié haute) ;
    5. retournement OFI/CVD : ratio taker buy/sell 5-min Vision (→ 1 h,
       moyenne) > 1 à t ET moyenne sur [t−3, t−1] < 1 (flux vendeur pendant
       la cascade). NB : les colonnes taker_buy_* des enriched 1h sont un
       PLACEHOLDER (= quote_volume/2, delta ≡ 0, constaté 2026-07-17) —
       inutilisables ; le vrai flux taker vient de Vision metrics.

  NOTE protocole : la première formulation (« cascade extrême ET delta
  positif à la MÊME heure ») était mécaniquement contradictoire (une heure
  de cascade est une heure de ventes) → 0 événement partout. Reformulée en
  setup[t−3,t]+déclencheur(t) AVANT tout calcul de rendement (seuls les
  comptes d'événements avaient été observés). Aucun seuil retouché ensuite.

  Entrée open t+1 (= close t), hold 8 h, sortie close t+8, coûts 30 bps
  A/R (×1/×2). Pas de holds superposés par actif.

  Tests de rejet (plan) : délai +1 barre ; coûts ×2 ; suppression des 10
  plus grosses cascades. Rapporté par actif BTC/ETH/SOL + pool alts.

  Ablation descriptive : baseline inconditionnelle, puis filtres 1,
  1-2, 1-3, 1-4, 1-5 (aucun n'est optimisé).

Caveats : liquidations cm = proxy corrélé des cascades um (le flux um
n'existe plus) ; la publication cm s'arrête au 2024-10-14 (constaté par
listing S3 : aucune clé 2025) → fenêtre utile ~16 mois 2023-06→2024-10 ;
le pool inter-actifs est corrélé (mêmes crashs) → n_jours distincts rapporté.

Sortie : reports/liq_cascade/CASCADE_EXHAUSTION.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[1]
LIQ_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_liquidation"
ENR_DIR = ROOT / "data" / "enriched"
OUT_DIR = ROOT / "reports" / "liq_cascade"

FLEET = {"BTCUSDT": ("BTCUSD_PERP", 100.0), "ETHUSDT": ("ETHUSD_PERP", 10.0),
         "SOLUSDT": ("SOLUSD_PERP", 10.0), "BNBUSDT": ("BNBUSD_PERP", 10.0),
         "XRPUSDT": ("XRPUSD_PERP", 10.0), "DOGEUSDT": ("DOGEUSD_PERP", 10.0),
         "ADAUSDT": ("ADAUSD_PERP", 10.0), "AVAXUSDT": ("AVAXUSD_PERP", 10.0),
         "LINKUSDT": ("LINKUSD_PERP", 10.0), "DOTUSDT": ("DOTUSD_PERP", 10.0)}

HOLD_H = 8
COST_RT = 0.0030
P_EXTREME = 0.99
ROLL_D = 90
OI_DROP = -0.02
FUND_Z_MAX = 0.5


MET_DIR = ROOT / "data" / "derivatives_backfill" / "binance_vision_metrics"
FUND_DIR = ROOT / "data" / "derivatives_backfill" / "binance" / "funding"


def load_symbol(sym: str):
    perp, csize = FLEET[sym]
    liq = pd.read_parquet(LIQ_DIR / f"{perp}_liq.parquet")
    liq["time"] = pd.to_datetime(liq["time"], utc=True)
    liq = liq[liq["side"].str.upper() == "SELL"]        # long liquidations
    qty = liq["accumulated_fill_quantity"].where(
        liq["accumulated_fill_quantity"] > 0, liq["original_quantity"])
    liq["notional"] = qty * csize
    liq_h = liq.set_index("time")["notional"].resample("H").sum()

    cols = ["datetime", "close", "quote_asset_volume",
            "taker_buy_quote", "close_position_in_range"]
    df = pd.read_parquet(ENR_DIR / f"{sym}_1h_enriched.parquet", columns=cols)
    df = df.set_index(pd.to_datetime(df.pop("datetime"), utc=True)).sort_index()
    df = df[~df.index.duplicated(keep="last")]

    m = pd.read_parquet(MET_DIR / f"{sym}_metrics_5m.parquet",
                        columns=["create_time", "sum_open_interest",
                                 "sum_taker_long_short_vol_ratio"])
    m = m.set_index(pd.to_datetime(m.pop("create_time"), utc=True)).sort_index()
    df["oi"] = m["sum_open_interest"].resample("H").last().reindex(df.index)
    # taker buy/sell vol ratio 5-min -> 1 h (moyenne). NB : les colonnes
    # taker_buy_* des enriched sont un PLACEHOLDER (= qv/2, delta nul) —
    # constate 2026-07-17, ne pas les utiliser.
    df["taker_ratio"] = (m["sum_taker_long_short_vol_ratio"]
                         .resample("H").mean().reindex(df.index))

    f = pd.read_parquet(FUND_DIR / f"{sym}.parquet",
                        columns=["timestamp", "funding_rate"])
    fr = (f.set_index(pd.to_datetime(f.pop("timestamp"), utc=True))
           .sort_index()["funding_rate"].resample("H").last().ffill())
    mu = fr.rolling(24 * 30, min_periods=24 * 10).mean()
    sd = fr.rolling(24 * 30, min_periods=24 * 10).std()
    df["funding_z"] = ((fr - mu) / sd).reindex(df.index)

    df["liq_long"] = liq_h.reindex(df.index).fillna(0.0)
    return df.loc[liq_h.index.min():liq_h.index.max()]


def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    w = 24 * ROLL_D
    p99 = df["liq_long"].rolling(w, min_periods=w // 3).quantile(P_EXTREME)
    cascade = (df["liq_long"] >= p99) & (df["liq_long"] > 0)
    c1 = cascade.rolling(4, min_periods=1).max().astype(bool)   # [t-3, t]
    c2 = (df["oi"] / df["oi"].shift(4) - 1) <= OI_DROP
    c3 = df["funding_z"] <= FUND_Z_MAX
    c4 = df["close_position_in_range"] >= 0.5
    c5 = ((df["taker_ratio"] > 1.0)
          & (df["taker_ratio"].rolling(3).mean().shift(1) < 1.0))
    return pd.DataFrame({"c1": c1, "c12": c1 & c2, "c123": c1 & c2 & c3,
                         "c1234": c1 & c2 & c3 & c4,
                         "full": c1 & c2 & c3 & c4 & c5})


def event_returns(df: pd.DataFrame, mask: pd.Series, cost_mult: float = 1.0,
                  delay: int = 0, drop_top: int = 0):
    """Rendements nets par événement, holds non superposés."""
    idx = df.index
    close = df["close"].values
    events, last_exit = [], -1
    sig_pos = np.flatnonzero(mask.values)
    if drop_top and len(sig_pos):
        liq = df["liq_long"].values[sig_pos]
        keep = np.argsort(liq)[:-drop_top] if len(sig_pos) > drop_top else []
        sig_pos = np.sort(sig_pos[keep]) if len(sig_pos) > drop_top else sig_pos[:0]
    for i in sig_pos:
        e_in = i + 1 + delay
        e_out = e_in + HOLD_H
        if e_in <= last_exit or e_out >= len(idx):
            continue
        gross = close[e_out] / close[e_in] - 1.0
        events.append((idx[i], gross - COST_RT * cost_mult))
        last_exit = e_out
    return pd.Series(dict(events), dtype=float)


def stats_block(r: pd.Series, df: pd.DataFrame = None) -> dict:
    if len(r) == 0:
        return {"n": 0}
    t = float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 else np.nan
    return {"n": int(len(r)), "n_days": int(r.index.normalize().nunique()),
            "mean_net": round(float(r.mean()), 4),
            "median_net": round(float(r.median()), 4),
            "win_rate": round(float((r > 0).mean()), 3),
            "t_stat": round(t, 2),
            "total_at_2pct": round(float((1 + r * 0.02).prod() - 1), 4)}


def main():
    per_asset, pooled = {}, {}
    all_events = {}
    for sym in FLEET:
        try:
            df = load_symbol(sym)
        except Exception as e:                            # noqa: BLE001
            per_asset[sym] = {"error": f"{type(e).__name__}: {e}"}
            continue
        sigs = build_signals(df)
        blk = {}
        # baseline inconditionnelle : même mécanique sur toutes les heures/8h
        base = df["close"].pct_change(HOLD_H).shift(-(HOLD_H + 1)).dropna()
        blk["baseline_uncond_8h_gross"] = {
            "mean": round(float(base.mean()), 5),
            "median": round(float(base.median()), 5)}
        for stage in ["c1", "c12", "c123", "c1234", "full"]:
            blk[stage] = stats_block(event_returns(df, sigs[stage]))
        blk["full_cost_x2"] = stats_block(
            event_returns(df, sigs["full"], cost_mult=2.0))
        blk["full_delay_plus1"] = stats_block(
            event_returns(df, sigs["full"], delay=1))
        blk["full_drop_top10"] = stats_block(
            event_returns(df, sigs["full"], drop_top=10))
        per_asset[sym] = blk
        all_events[sym] = event_returns(df, sigs["full"])
        print(f"{sym:9} full n={blk['full'].get('n',0):3} "
              f"mean={blk['full'].get('mean_net',float('nan')):+.4f} "
              f"win={blk['full'].get('win_rate',float('nan'))}", flush=True)

    alts = pd.concat([v for k, v in all_events.items()
                      if k not in ("BTCUSDT", "ETHUSDT", "SOLUSDT")])
    majors = pd.concat([v for k, v in all_events.items()
                        if k in ("BTCUSDT", "ETHUSDT", "SOLUSDT")])
    pooled["alts_pooled_full"] = stats_block(alts.sort_index())
    pooled["majors_pooled_full"] = stats_block(majors.sort_index())
    pooled["all_pooled_full"] = stats_block(
        pd.concat(list(all_events.values())).sort_index())

    out = {"experiment": "CASCADE_EXHAUSTION", "preregistered": True,
           "protocol": {"p_extreme": P_EXTREME, "roll_days": ROLL_D,
                        "oi_drop_4h": OI_DROP, "funding_z_max": FUND_Z_MAX,
                        "absorption": "close_position_in_range>=0.5",
                        "cvd": "delta>0 & sum(delta[t-4..t-1])<0",
                        "hold_h": HOLD_H, "cost_rt_bps": COST_RT * 1e4,
                        "entry": "open t+1", "no_overlap": True},
           "per_asset": per_asset, "pooled": pooled,
           "environment": {
               "liq_source": "cm liquidationSnapshot (proxy um), "
                             "publication stoppee 2024-10-14",
               "generated_at": pd.Timestamp.utcnow().isoformat(),
               "command": ".venv/bin/python scripts/backtest_cascade_exhaustion.py",
               "python": sys.version.split()[0]}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "CASCADE_EXHAUSTION.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(pooled, indent=2))


if __name__ == "__main__":
    main()
