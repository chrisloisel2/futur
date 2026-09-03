"""V1 — SHORT_COVERING_CONTINUATION : validation indépendante de l'alpha shadow
RECONSTRUCTED (jamais validé).

Spec exécutée : ../SHORT_COVERING_CONTINUATION/PREREGISTRATION.md.
Réimplémentation depuis la définition économique (prix dans la queue haute + OI dans la
queue basse, centiles causaux 720 h, barre courante exclue). Le code de l'engine live
n'a pas été lu ; seul `freeze_spec.json` l'a été, en lecture seule.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl                                          # noqa: E402
import exp_v2_cascade as ev2                                         # noqa: E402
from exp_event_weighted_cluster import ew_cluster, diff_ew_cluster   # noqa: E402

OUT = "/home/qbee/futur/reports/edge_discovery/validation_2026-09/_lib/out"
SCRATCH = os.environ.get(
    "VAL_SCRATCH",
    "/tmp/claude-1000/-home-qbee-futur/96533575-ccfe-4d52-a4ae-a61df9219e6e/scratchpad/validation_wave2")
HOURLY = os.path.join(SCRATCH, "sc_hourly.parquet")
METRICS = "/home/qbee/futur/data/derivatives_backfill/binance_vision_metrics"
UNIVERSE_CFG = "/home/qbee/futur/configs/portfolio_v1_1_parallel_50.yaml"
LEDGER = "/home/qbee/futur/reports/live_alpha_lab/SHORT_COVERING_CONTINUATION_V1/decisions.parquet"

WINDOW = 720          # heures de référence (30 j)
COST, STRESS = 14.0, 28.0


def build_hourly() -> pd.DataFrame:
    """Barres horaires prix + OI, jointes sur l'heure UTC, par symbole de l'univers figé."""
    if os.path.exists(HOURLY):
        return pd.read_parquet(HOURLY)
    universe = yaml.safe_load(open(UNIVERSE_CFG))["universe"]
    con = vl.duckdb_connect()
    frames = []
    for i, sym in enumerate(universe):
        mp = f"{METRICS}/{sym}_metrics_5m.parquet"
        if not os.path.exists(mp):
            print(f"  [skip] {sym}: pas de metrics", flush=True)
            continue
        q = f"""
        WITH px AS (
            SELECT date_trunc('hour', timestamp) AS h,
                   arg_max(close, timestamp) AS close
            FROM read_parquet('/home/qbee/futur-data-v2/data_v2/normalized/perp_ohlcv/venue=binance/symbol={sym}/*/*.parquet')
            GROUP BY 1
        ), oi AS (
            SELECT date_trunc('hour', create_time) AS h,
                   arg_max(sum_open_interest, create_time)       AS oi,
                   arg_max(sum_open_interest_value, create_time) AS oi_val
            FROM read_parquet('{mp}')
            GROUP BY 1
        )
        SELECT '{sym}' AS symbol, px.h AS ts, px.close, oi.oi, oi.oi_val
        FROM px JOIN oi USING (h)
        ORDER BY 1, 2
        """
        try:
            frames.append(con.execute(q).df())
        except Exception as e:                                   # noqa: BLE001
            print(f"  [skip] {sym}: {str(e)[:80]}", flush=True)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(universe)} symboles", flush=True)
    con.close()
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(HOURLY, index=False)
    print(f"  panel horaire: {len(df)} lignes, {df.symbol.nunique()} symboles "
          f"({os.path.getsize(HOURLY)/1e6:.1f}MB)", flush=True)
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Rendements/variations 1 h + rangs centiles causaux sur 720 h, barre courante exclue.

    `rolling(WINDOW+1).rank()` donne le rang de la barre courante parmi les 721 valeurs
    [t−720h, t] ; `(rang − 1) / WINDOW` est donc la proportion d'antécédents STRICTEMENT
    inférieurs — la barre courante est bien retirée de sa propre référence.
    """
    out = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("ts").copy()
        # grille horaire sans trou : un trou reste NaN, jamais rempli
        g = g.set_index("ts").reindex(
            pd.date_range(g["ts"].min(), g["ts"].max(), freq="h")).rename_axis("ts")
        g["symbol"] = sym
        g["px_ret_1h"] = g["close"].pct_change()
        g["oi_delta_1h"] = g["oi"].pct_change()
        g["oi_val_delta_1h"] = g["oi_val"].pct_change()
        for col, name in (("px_ret_1h", "px_p"), ("oi_delta_1h", "oi_p"),
                          ("oi_val_delta_1h", "oiv_p")):
            r = g[col].rolling(WINDOW + 1, min_periods=WINDOW + 1).rank()
            g[name] = (r - 1.0) / WINDOW
        for col, name in (("px_ret_1h", "px_p360"), ("oi_delta_1h", "oi_p360")):
            r = g[col].rolling(361, min_periods=361).rank()
            g[name] = (r - 1.0) / 360
        # rendements forward
        for h in (1, 4, 8):
            g[f"fwd_{h}h"] = g["close"].shift(-h) / g["close"] - 1.0
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


def run(df: pd.DataFrame, is_a: np.ndarray, fwd: str, label: str,
        cost: float = COST, stress: float = STRESS) -> dict:
    # le flag du bras doit voyager AVEC la ligne : on le pose en colonne avant tout
    # tri/filtrage, sinon un realignement positionnel après sort_values mélange les bras.
    d = df.copy()
    d["_A"] = np.asarray(is_a, dtype=bool)
    d = d[d[fwd].notna()].copy()
    d = d.rename(columns={"ts": "event_time"})
    d["fwd_4h"] = d[fwd]
    d = d.sort_values("event_time").reset_index(drop=True)
    d = ev2.add_declustering(d)
    A, B = d[d["_A"] == True], d[d["_A"] == False]      # noqa: E712
    if len(A) < 50:
        return {"label": label, "error": f"bras A trop mince ({len(A)})"}
    res = {
        "label": label, "n_A": int(len(A)), "n_B": int(len(B)),
        "episode_A": ev2.gate_arm(A, cost=cost, stress=stress),
        "episode_B": ev2.gate_arm(B, cost=cost, stress=stress),
        "episode_A_minus_B": ev2.arm_difference(A, B),
        "event_A": ew_cluster(A, f"{label} A"),
        "event_A_minus_B": diff_ew_cluster(A, B, f"{label} A−B"),
    }
    ea, ev, d1, d2 = (res["episode_A"], res["event_A"],
                      res["episode_A_minus_B"], res["event_A_minus_B"])
    print(f"  {label:34s} nA={len(A):6d} L3={ea['n_independent_L3']:5d} | "
          f"ep net14={ea['net_bps']:8.2f} t={str(ea['t_stat_declustered']):>7s} | "
          f"ev net14={ev['net14_event_weighted']:8.2f} t_cl={str(ev['t_cluster_robust']):>6s} | "
          f"A−B ep={d1.get('difference_bps')} ev={d2.get('difference_bps')} "
          f"t={d2.get('t_cluster_robust')}", flush=True)
    return res


def main():
    print("[build] panel horaire prix+OI", flush=True)
    raw = build_hourly()
    print("[features] centiles causaux 720h", flush=True)
    df = add_features(raw)
    df = df[df["ts"] >= pd.Timestamp("2022-01-01", tz="UTC")].reset_index(drop=True)
    elig = df["px_p"].notna() & df["oi_p"].notna()
    df = df[elig].reset_index(drop=True)
    print(f"  population éligible: {len(df)} barres, {df.symbol.nunique()} symboles, "
          f"{df.ts.min()} → {df.ts.max()}", flush=True)

    res = {"_population": {"n_bars": int(len(df)), "n_symbols": int(df.symbol.nunique()),
                           "window": [str(df.ts.min()), str(df.ts.max())]}}

    A = ((df["px_p"] >= 0.90) & (df["oi_p"] <= 0.10)).to_numpy()
    print("[PRIMARY + perturbations]", flush=True)
    res["PRIMARY"] = run(df, A, "fwd_4h", "PRIMARY decile 720h fwd4h")
    res["P1_quintile"] = run(df, ((df["px_p"] >= 0.80) & (df["oi_p"] <= 0.20)).to_numpy(),
                             "fwd_4h", "P1 quintile")
    res["P2_window360"] = run(df, ((df["px_p360"] >= 0.90) & (df["oi_p360"] <= 0.10)).to_numpy(),
                              "fwd_4h", "P2 fenêtre 360h")
    score = np.minimum(df["px_p"].to_numpy(), 1.0 - df["oi_p"].to_numpy())
    res["P3_live_score"] = run(df, score >= 0.90, "fwd_4h", "P3 score live min()")
    res["P4_fwd1h"] = run(df, A, "fwd_1h", "P4 horizon 1h")
    res["P4_fwd8h"] = run(df, A, "fwd_8h", "P4 horizon 8h")
    res["P6_cost150"] = run(df, A, "fwd_4h", "P6 coût +50%", cost=21.0, stress=42.0)
    res["P8_oi_notional"] = run(df, ((df["px_p"] >= 0.90) & (df["oiv_p"] <= 0.10)).to_numpy(),
                                "fwd_4h", "P8 OI notionnel")

    # hors 2022 (régime de départ)
    d5 = df[df["ts"] >= pd.Timestamp("2023-01-01", tz="UTC")].reset_index(drop=True)
    res["P5_ex2022"] = run(d5, ((d5["px_p"] >= 0.90) & (d5["oi_p"] <= 0.10)).to_numpy(),
                           "fwd_4h", "P5 hors 2022")

    # ── accord avec le ledger live ────────────────────────────────────────
    if os.path.exists(LEDGER):
        led = pd.read_parquet(LEDGER)
        tcol = next((c for c in ("event_time", "decision_ts", "ts", "timestamp")
                     if c in led.columns), None)
        if tcol:
            led[tcol] = pd.to_datetime(led[tcol], utc=True)
            mine = df[A].copy()
            mine["h"] = pd.to_datetime(mine["ts"], utc=True).dt.floor("h")
            led["h"] = led[tcol].dt.floor("h")
            ks_mine = set(zip(mine["symbol"], mine["h"]))
            ks_led = set(zip(led["symbol"], led["h"])) if "symbol" in led.columns else set()
            inter = ks_mine & ks_led
            res["ledger_agreement"] = {
                "n_ledger_rows": int(len(led)),
                "n_ledger_hours": len(ks_led),
                "n_mine_in_common_window": int(sum(
                    1 for s, h in ks_mine
                    if led[tcol].min() <= h <= led[tcol].max())),
                "n_agreed": len(inter),
                "ledger_window": [str(led[tcol].min()), str(led[tcol].max())],
                "note": "accord = même (symbole, heure UTC) classé SHORT_COVERING",
            }
            print("  ledger:", json.dumps(res["ledger_agreement"], default=str)[:240], flush=True)

    # ── chevauchement avec la population cascade ──────────────────────────
    try:
        casc = ev2.population_A()
        casc_keys = set(zip(casc["symbol"], casc["event_time"].dt.floor("h")))
        mine = df[A]
        mk = set(zip(mine["symbol"], pd.to_datetime(mine["ts"], utc=True).dt.floor("h")))
        res["overlap_with_cascade_population"] = {
            "n_arm_A_hours": len(mk),
            "n_shared_hours": len(mk & casc_keys),
            "share": round(len(mk & casc_keys) / max(1, len(mk)), 4),
        }
        print("  overlap cascade:", res["overlap_with_cascade_population"], flush=True)
    except Exception as e:                                   # noqa: BLE001
        res["overlap_with_cascade_population"] = {"error": str(e)}

    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/v1_short_covering_raw.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("\nécrit:", f"{OUT}/v1_short_covering_raw.json")


if __name__ == "__main__":
    main()
