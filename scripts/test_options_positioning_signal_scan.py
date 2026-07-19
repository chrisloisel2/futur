#!/usr/bin/env python3
"""
scripts/test_options_positioning_signal_scan.py
─────────────────────────────────────────────────────────────────────────────
Scan de signal OPTIONS_POSITIONING (≠ VRP) : les features de positionnement
reconstruites des trades Deribit (skew tradé, P/C, flows signés, concentration
de strikes, blocs) prédisent-elles les retours BTC forward — ou au moins le
RISQUE (vol réalisée future) pour servir de filtre aux moteurs existants ?

Causal strict :
  - features du jour D = trades de D (complètes à D 23:59:59) ;
  - entrée au close D (frontière de jour) ET variante délai +24 h ;
  - z-scores roulants 90 j, jamais centrés sur le futur.

Multiple testing : 9 signaux × 3 horizons × 2 délais = 54 tests — seuil de
sérieux p < 0.002 (~Bonferroni), tout le reste = bruit à ignorer.

    .venv/bin/python scripts/test_options_positioning_signal_scan.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEATS = ROOT / "data" / "options_backfill" / "deribit" / "features" / "BTC_daily.parquet"
ENRICHED = ROOT / "data" / "enriched" / "BTCUSDT_1h_enriched.parquet"
REPORT = ROOT / "reports" / "OPTIONS_POSITIONING_SIGNAL_SCAN.md"

SIGNALS = ["d_skew_25ish", "skew_25ish", "d_atm_iv_traded", "d_pc_volume_ratio",
           "pc_volume_ratio", "net_call_flow_btc", "net_put_flow_btc",
           "top_strike_share", "block_share"]
FWD_D = [1, 3, 7]
P_SERIOUS = 0.002


def zroll(s: pd.Series, w: int = 90) -> pd.Series:
    return (s - s.rolling(w, min_periods=30).mean()) / s.rolling(w, min_periods=30).std()


def main() -> None:
    feats = pd.read_parquet(FEATS).set_index("day").sort_index()
    px = pd.read_parquet(ENRICHED, columns=["datetime", "close"])
    px = px.set_index(pd.DatetimeIndex(px["datetime"]))["close"].sort_index()
    daily = px.resample("D").last()          # close du dernier bar du jour D

    df = feats.copy()
    df["close"] = daily.reindex(df.index)
    df = df.dropna(subset=["close"])
    rets = np.log(df["close"]).diff()

    lines = ["# Scan signal OPTIONS_POSITIONING (Deribit trades → BTC forward)",
             f"\n{len(df)} jours, {df.index.min().date()} → {df.index.max().date()}. "
             f"54 tests directionnels ; seuil de sérieux p < {P_SERIOUS}.\n", "```"]
    hdr = (f"{'signal':<20}{'delai':>6}{'fwd_d':>6}{'IC':>8}{'p':>10}{'Q5-Q1 bps':>11}")
    print(f"{len(df)} jours, {df.index.min().date()} → {df.index.max().date()}\n")
    print(hdr); lines.append(hdr)
    print("─" * 62); lines.append("─" * 62)

    serious = []
    for sig in SIGNALS:
        z = zroll(df[sig])
        for delay in [0, 1]:
            zd = z.shift(delay)
            for h in FWD_D:
                fwd = df["close"].shift(-(delay + h)) / df["close"].shift(-delay) - 1
                m = zd.notna() & fwd.notna()
                if m.sum() < 200:
                    continue
                ic, p = spearmanr(zd[m], fwd[m])
                q = pd.qcut(zd[m], 5, labels=False, duplicates="drop")
                spread = (fwd[m][q == q.max()].mean() - fwd[m][q == 0].mean()) * 1e4
                flag = "  ◀ SÉRIEUX" if p < P_SERIOUS else ""
                row = f"{sig:<20}{delay:>6}{h:>6}{ic:>8.3f}{p:>10.1e}{spread:>11.1f}{flag}"
                print(row); lines.append(row)
                if p < P_SERIOUS:
                    serious.append((sig, delay, h, ic, p, spread))
    lines.append("```")

    # Volet FILTRE : stress de positionnement → vol réalisée / retours des 7 j suivants
    fwd_ret_7 = df["close"].shift(-7) / df["close"] - 1
    fwd_vol_7 = rets.shift(-7).rolling(7).std() * np.sqrt(365)   # vol des 7 j APRÈS D
    title = "\n=== Volet FILTRE : |stress positionnement| → risque 7 j suivant ==="
    print(title); lines += [title, "```"]
    for sig in ["d_skew_25ish", "d_atm_iv_traded", "d_pc_volume_ratio"]:
        z = zroll(df[sig]).abs()
        hi = z > 2
        m = z.notna() & fwd_vol_7.notna() & fwd_ret_7.notna()
        if (hi & m).sum() < 20:
            continue
        r = (f"|z({sig})|>2 : n={int((hi & m).sum())}, "
             f"vol7f {fwd_vol_7[m & hi].mean()*100:.0f}% vs base {fwd_vol_7[m & ~hi].mean()*100:.0f}%, "
             f"ret7f {fwd_ret_7[m & hi].mean()*1e4:+.0f} bps vs {fwd_ret_7[m & ~hi].mean()*1e4:+.0f}")
        print(r); lines.append(r)
    lines.append("```")

    concl = (f"\n## Verdict brut\n\n{len(serious)} test(s) directionnel(s) sous p<{P_SERIOUS} "
             f"sur 54. " +
             ("Candidats à valider OOS + coûts ×2 + corrélation stack :\n" +
              "\n".join(f"- {s} (delay {d}, fwd {h}j) : IC {ic:+.3f}, p {p:.1e}, "
                        f"Q5-Q1 {sp:+.0f} bps" for s, d, h, ic, p, sp in serious)
              if serious else
              "Aucun signal directionnel sérieux — l'info options ne prédit pas le "
              "RENDEMENT BTC au quotidien sur 2023-2026 avec ces features v0. "
              "Reste le volet filtre/risque ci-dessus."))
    print(concl)
    lines.append(concl)
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\nRapport → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
