"""
scripts/audit_short_failures.py
================================
Analyse post-mortem des trades SHORT perdants.

Usage:
  python scripts/audit_short_failures.py
  python scripts/audit_short_failures.py --data-dir data/trades
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "short_rebuild"

SEARCH_DIRS = [
    ROOT / "reports",
    ROOT / "data",
    ROOT / "runs",
]
TRADE_PATTERNS = [
    "*short*trades*",
    "*short*results*",
    "*trades*short*",
    "*results*short*",
]

# ─── Constantes de classification ────────────────────────────────────────────
COST_PCT = 0.0012
SQUEEZE_LIMIT = 0.018
LATE_ENTRY_ATR = 2.5
FUNDING_WRONG_THRESHOLD = -0.0003

CATEGORIES = [
    "squeeze_loss",
    "late_short_after_flush",
    "bull_trend_short",
    "no_breakdown_followthrough",
    "cost_drag",
    "bad_regime",
    "random_noise",
]


# ─── Recherche de données réelles ────────────────────────────────────────────

def find_trade_files(data_dir: Optional[Path] = None) -> list[Path]:
    search_roots = [data_dir] if data_dir else SEARCH_DIRS
    found = []
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in TRADE_PATTERNS:
            found.extend(root.rglob(pattern))
    return sorted(set(found))


def load_trade_files(paths: list[Path]) -> Optional[pd.DataFrame]:
    frames = []
    for p in paths:
        try:
            if p.suffix == ".parquet":
                frames.append(pd.read_parquet(p))
            else:
                frames.append(pd.read_csv(p))
            print(f"   Chargé : {p.relative_to(ROOT)}  ({len(frames[-1])} lignes)")
        except Exception as exc:
            print(f"   Ignoré {p.name} : {exc}")
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return df


# ─── Génération de données synthétiques ──────────────────────────────────────

def _rng_seed(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def generate_synthetic_trades(n: int = 500) -> pd.DataFrame:
    print(f"   Aucune donnée réelle trouvée — génération de {n} trades synthétiques (2020-2025)")
    rng = _rng_seed()

    dates = pd.date_range("2020-01-01", "2025-01-01", periods=n, freq=None)
    years = dates.year

    symbols = rng.choice(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"], size=n,
                         p=[0.40, 0.30, 0.15, 0.10, 0.05])

    regimes = rng.choice(["SHORTABLE", "NEUTRAL", "NO_SHORT"], size=n, p=[0.30, 0.45, 0.25])

    rv = rng.gamma(shape=2.0, scale=0.015, size=n).clip(0.005, 0.12)
    vol_bucket = pd.cut(rv, bins=[0, 0.01, 0.025, 0.05, 1.0],
                        labels=["low", "medium", "high", "extreme"])

    funding_rate = rng.normal(loc=0.0002, scale=0.0008, size=n)
    funding_bucket = pd.cut(funding_rate,
                            bins=[-np.inf, -0.0005, 0.0, 0.0005, np.inf],
                            labels=["very_negative", "negative", "positive", "very_positive"])

    oi_change = rng.normal(loc=0.0, scale=0.03, size=n)
    dist_vwap = rng.normal(loc=-0.005, scale=0.015, size=n)
    dist_ema200 = rng.normal(loc=-0.02, scale=0.04, size=n)
    rsi = rng.uniform(20, 80, size=n)
    momentum_72h = rng.normal(loc=-0.005, scale=0.03, size=n)

    entry_price = rng.uniform(20_000, 70_000, size=n)

    base_pnl = rng.normal(loc=-0.003, scale=0.025, size=n)

    regime_penalty = np.where(regimes == "NO_SHORT", -0.012,
                     np.where(regimes == "SHORTABLE", 0.006, 0.0))
    funding_bonus = np.where(funding_rate < 0, 0.003, -0.002)
    vol_bonus = np.where(rv > 0.04, -0.008, 0.0)
    pnl_pct = (base_pnl + regime_penalty + funding_bonus + vol_bonus).clip(-0.15, 0.10)

    exit_price = entry_price * np.exp(-pnl_pct)

    hold_hours = rng.integers(1, 49, size=n).astype(float)

    intra_drawdown_pct = rng.exponential(scale=0.008, size=n).clip(0, 0.08)
    squeeze_adverse_pct = np.where(
        rng.random(n) < 0.18,
        rng.uniform(SQUEEZE_LIMIT * 0.8, SQUEEZE_LIMIT * 2.5, size=n),
        rng.uniform(0, SQUEEZE_LIMIT * 0.7, size=n),
    )

    df = pd.DataFrame({
        "trade_date":         dates,
        "symbol":             symbols,
        "year":               years,
        "entry_price":        entry_price,
        "exit_price":         exit_price,
        "pnl_pct":            pnl_pct,
        "regime":             regimes,
        "volatility_bucket":  vol_bucket,
        "funding_rate":       funding_rate,
        "funding_bucket":     funding_bucket,
        "open_interest_change": oi_change,
        "dist_vwap":          dist_vwap,
        "dist_ema200":        dist_ema200,
        "rsi_at_entry":       rsi,
        "momentum_72h":       momentum_72h,
        "intra_drawdown_pct": intra_drawdown_pct,
        "squeeze_adverse_pct": squeeze_adverse_pct,
        "hold_hours":         hold_hours,
    })
    return df


# ─── Nettoyage / normalisation ────────────────────────────────────────────────

REQUIRED_COLS = [
    "pnl_pct", "regime", "volatility_bucket", "funding_rate",
    "dist_vwap", "rsi_at_entry", "momentum_72h",
    "intra_drawdown_pct", "squeeze_adverse_pct",
]

OPTIONAL_DEFAULTS: dict = {
    "year":               lambda df: (pd.to_datetime(df["trade_date"]).dt.year
                                      if "trade_date" in df.columns
                                      else pd.Series(0, index=df.index)),
    "symbol":             lambda df: pd.Series("UNKNOWN", index=df.index),
    "entry_price":        lambda df: pd.Series(np.nan, index=df.index),
    "exit_price":         lambda df: pd.Series(np.nan, index=df.index),
    "hold_hours":         lambda df: pd.Series(np.nan, index=df.index),
    "open_interest_change": lambda df: pd.Series(0.0, index=df.index),
    "dist_ema200":        lambda df: pd.Series(0.0, index=df.index),
    "funding_bucket":     lambda df: pd.cut(
        df["funding_rate"] if "funding_rate" in df.columns else pd.Series(0.0, index=df.index),
        bins=[-np.inf, -0.0005, 0.0, 0.0005, np.inf],
        labels=["very_negative", "negative", "positive", "very_positive"],
    ),
}


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in REQUIRED_COLS:
        if col not in df.columns:
            df[col] = np.nan
    for col, factory in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = factory(df)
    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")
    df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce").fillna(0.0)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["pnl_pct"])
    return df


# ─── Bucketing helper ─────────────────────────────────────────────────────────

def _vwap_bucket(df: pd.DataFrame) -> pd.Series:
    if "dist_vwap" not in df.columns:
        return pd.Series("unknown", index=df.index)
    return pd.cut(
        df["dist_vwap"].fillna(0),
        bins=[-np.inf, -0.02, -0.005, 0.005, 0.02, np.inf],
        labels=["far_below", "below", "at_vwap", "above", "far_above"],
    ).astype(str)


# ─── Classification des trades perdants ──────────────────────────────────────

def classify_losing_trade(row: pd.Series) -> str:
    pnl = row.get("pnl_pct", 0.0)
    squeeze = row.get("squeeze_adverse_pct", 0.0)
    regime = row.get("regime", "NEUTRAL")
    funding = row.get("funding_rate", 0.0)
    rsi = row.get("rsi_at_entry", 50.0)
    mom72 = row.get("momentum_72h", 0.0)
    drawdown = row.get("intra_drawdown_pct", 0.0)
    dist_vwap = row.get("dist_vwap", 0.0)

    if np.isnan(pnl) or pnl >= 0:
        return "random_noise"

    if squeeze >= SQUEEZE_LIMIT:
        return "squeeze_loss"

    if regime == "NO_SHORT":
        return "bull_trend_short"

    if dist_vwap < -0.02 and mom72 < -0.02:
        return "late_short_after_flush"

    if rsi > 60 and mom72 > 0.015:
        return "bull_trend_short"

    if funding < FUNDING_WRONG_THRESHOLD:
        return "bad_regime"

    if drawdown < abs(pnl) * 0.5 and abs(pnl) < COST_PCT * 3:
        return "cost_drag"

    if drawdown > 0 and drawdown < SQUEEZE_LIMIT * 0.5:
        return "no_breakdown_followthrough"

    return "random_noise"


def classify_losses_vectorized(df: pd.DataFrame) -> pd.Series:
    losers = df["pnl_pct"] < 0
    categories = pd.Series("random_noise", index=df.index, dtype=str)

    squeeze = df["squeeze_adverse_pct"].fillna(0)
    regime = df["regime"].fillna("NEUTRAL")
    funding = df["funding_rate"].fillna(0)
    rsi = df["rsi_at_entry"].fillna(50)
    mom72 = df["momentum_72h"].fillna(0)
    drawdown = df["intra_drawdown_pct"].fillna(0)
    dist_vwap_col = df["dist_vwap"].fillna(0)
    pnl = df["pnl_pct"]

    is_squeeze = losers & (squeeze >= SQUEEZE_LIMIT)
    is_bull_trend = losers & ~is_squeeze & (
        (regime == "NO_SHORT") | ((rsi > 60) & (mom72 > 0.015))
    )
    is_late = losers & ~is_squeeze & ~is_bull_trend & (dist_vwap_col < -0.02) & (mom72 < -0.02)
    is_bad_regime = losers & ~is_squeeze & ~is_bull_trend & ~is_late & (funding < FUNDING_WRONG_THRESHOLD)
    is_cost_drag = (
        losers & ~is_squeeze & ~is_bull_trend & ~is_late & ~is_bad_regime
        & (drawdown < pnl.abs() * 0.5) & (pnl.abs() < COST_PCT * 3)
    )
    is_no_breakdown = (
        losers & ~is_squeeze & ~is_bull_trend & ~is_late & ~is_bad_regime & ~is_cost_drag
        & (drawdown > 0) & (drawdown < SQUEEZE_LIMIT * 0.5)
    )

    categories[is_squeeze] = "squeeze_loss"
    categories[is_bull_trend] = "bull_trend_short"
    categories[is_late] = "late_short_after_flush"
    categories[is_bad_regime] = "bad_regime"
    categories[is_cost_drag] = "cost_drag"
    categories[is_no_breakdown] = "no_breakdown_followthrough"
    categories[~losers] = "winner"

    return categories


# ─── Métriques de performance par groupe ─────────────────────────────────────

def compute_group_metrics(group: pd.DataFrame) -> pd.Series:
    pnl = group["pnl_pct"].dropna()
    if len(pnl) == 0:
        return pd.Series(dtype=float)

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    n = len(pnl)
    win_rate = len(wins) / n if n > 0 else np.nan
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0
    gross_win = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else np.nan
    pf = gross_win / gross_loss if (gross_loss and gross_loss > 0) else np.nan
    expectancy = pnl.mean()

    squeeze = group["squeeze_adverse_pct"].fillna(0) if "squeeze_adverse_pct" in group.columns \
              else pd.Series(0.0, index=group.index)
    mae_mean = squeeze.mean()
    squeeze_loss_pct = (squeeze >= SQUEEZE_LIMIT).mean()

    late_mask = (group.get("dist_vwap", pd.Series(0.0, index=group.index)).fillna(0) < -0.02) & \
                (group.get("momentum_72h", pd.Series(0.0, index=group.index)).fillna(0) < -0.02)
    late_short_loss_pct = late_mask.mean()

    bull_mask = (group.get("regime", pd.Series("NEUTRAL", index=group.index)) == "NO_SHORT") | \
                ((group.get("rsi_at_entry", pd.Series(50.0, index=group.index)).fillna(50) > 60) &
                 (group.get("momentum_72h", pd.Series(0.0, index=group.index)).fillna(0) > 0.015))
    bull_trend_short_loss_pct = bull_mask.mean()

    funding = group.get("funding_rate", pd.Series(0.0, index=group.index)).fillna(0)
    funding_wrong_side_pct = (funding < FUNDING_WRONG_THRESHOLD).mean()

    return pd.Series({
        "n_trades":                 n,
        "win_rate":                 round(win_rate, 4),
        "profit_factor":            round(pf, 4) if not np.isnan(pf) else np.nan,
        "expectancy":               round(expectancy, 6),
        "avg_win":                  round(avg_win, 6),
        "avg_loss":                 round(avg_loss, 6),
        "max_loss":                 round(pnl.min(), 6),
        "max_adverse_excursion_mean": round(mae_mean, 6),
        "squeeze_loss_pct":         round(squeeze_loss_pct, 4),
        "late_short_loss_pct":      round(late_short_loss_pct, 4),
        "bull_trend_short_loss_pct": round(bull_trend_short_loss_pct, 4),
        "funding_wrong_side_pct":   round(funding_wrong_side_pct, 4),
    })


# ─── Analyse par dimension ────────────────────────────────────────────────────

def analyse_by_dimension(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df.copy()
    series = df[col]
    if hasattr(series, "cat"):
        series = series.astype(str)
    df[col] = series.fillna("unknown").astype(str)
    result = df.groupby(col, observed=True).apply(compute_group_metrics)
    if col in result.columns:
        return result.reset_index(drop=True).assign(**{col: result.index}).reset_index(drop=True)
    return result.reset_index()


def analyse_all_dimensions(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    results = {}
    for dim in ["year", "regime", "volatility_bucket", "funding_bucket"]:
        if dim in df.columns:
            results[dim] = analyse_by_dimension(df, dim)

    df = df.copy()
    df["vwap_bucket"] = _vwap_bucket(df)
    results["vwap_bucket"] = analyse_by_dimension(df, "vwap_bucket")
    return results


# ─── Rapport textuel ─────────────────────────────────────────────────────────

def build_text_report(
    df: pd.DataFrame,
    dim_tables: dict[str, pd.DataFrame],
    loss_categories: pd.Series,
) -> str:
    lines = []
    app = lines.append

    n = len(df)
    losers = df["pnl_pct"] < 0
    n_loss = losers.sum()
    winners = df["pnl_pct"] >= 0
    n_win = winners.sum()
    overall_wr = n_win / n if n > 0 else 0.0
    gross_win = df.loc[winners, "pnl_pct"].sum()
    gross_loss = abs(df.loc[losers, "pnl_pct"].sum())
    overall_pf = gross_win / gross_loss if gross_loss > 0 else float("nan")
    overall_exp = df["pnl_pct"].mean()

    app("=" * 72)
    app("  AUDIT POST-MORTEM — TRADES SHORT")
    app("=" * 72)
    app(f"  Total trades  : {n:,}")
    app(f"  Gagnants      : {n_win:,}  ({overall_wr:.1%})")
    app(f"  Perdants      : {n_loss:,}  ({n_loss/n:.1%})")
    app(f"  Profit Factor : {overall_pf:.3f}")
    app(f"  Expectancy    : {overall_exp:.4%} / trade")
    app("")

    # --- Catégories de pertes
    app("─" * 72)
    app("  CATÉGORIES DE PERTES (trades perdants seulement)")
    app("─" * 72)
    cat_counts = loss_categories[losers].value_counts()
    for cat in CATEGORIES:
        cnt = cat_counts.get(cat, 0)
        pct = cnt / n_loss if n_loss > 0 else 0.0
        app(f"  {cat:<35s} : {cnt:>5d}  ({pct:.1%})")
    app("")

    # --- Par dimension
    for dim, tbl in dim_tables.items():
        app("─" * 72)
        app(f"  PAR {dim.upper()}")
        app("─" * 72)
        if tbl.empty:
            app("  (aucune donnée)")
            continue
        pf_col = "profit_factor"
        wr_col = "win_rate"
        for _, row in tbl.iterrows():
            key = row.iloc[0]
            pf = row.get(pf_col, np.nan)
            wr = row.get(wr_col, np.nan)
            n_t = int(row.get("n_trades", 0))
            exp_ = row.get("expectancy", np.nan)
            pf_str = f"{pf:.3f}" if not np.isnan(pf) else " N/A "
            wr_str = f"{wr:.1%}" if not np.isnan(wr) else " N/A "
            exp_str = f"{exp_:.4%}" if not np.isnan(exp_) else " N/A "
            flag = " ✗" if (not np.isnan(pf) and pf < 0.80) else \
                   " ~" if (not np.isnan(pf) and pf < 1.0) else ""
            app(f"  {str(key):<20s}  n={n_t:>5d}  WR={wr_str}  PF={pf_str}  Exp={exp_str}{flag}")
        app("")

    # --- Verdict régime
    app("─" * 72)
    app("  VERDICT PAR RÉGIME")
    app("─" * 72)
    if "regime" in dim_tables:
        for _, row in dim_tables["regime"].iterrows():
            reg = row.iloc[0]
            pf = row.get("profit_factor", np.nan)
            verdict = (
                "BLOQUER"   if (not np.isnan(pf) and pf < 0.80) else
                "SURVEILLER" if (not np.isnan(pf) and pf < 1.0) else
                "POSSIBLE"  if (not np.isnan(pf) and pf < 1.15) else
                "OK"
            )
            app(f"  {str(reg):<20s}  PF={pf:.3f}  → {verdict}")
    app("")

    # --- Synthèse
    app("─" * 72)
    app("  CE QUI TUE LE SHORT")
    app("─" * 72)
    top_killers = cat_counts.head(3)
    for cat, cnt in top_killers.items():
        pct = cnt / n_loss if n_loss > 0 else 0.0
        explanations = {
            "squeeze_loss":               "position liquidée par un spike haussier court",
            "bull_trend_short":           "short contre tendance haussière structurelle",
            "late_short_after_flush":     "entrée tardive après un flush déjà réalisé",
            "no_breakdown_followthrough": "prix casse sans continuation — faux signal",
            "cost_drag":                  "move trop petit, coûts absorbent le P&L",
            "bad_regime":                 "funding/régime défavorable au short",
            "random_noise":               "signal bruité sans edge mesurable",
        }
        expl = explanations.get(cat, "")
        app(f"  [{pct:.1%}] {cat} — {expl}")
    app("")

    app("─" * 72)
    app("  RÉGIMES OÙ LE SHORT A UNE CHANCE")
    app("─" * 72)
    if "regime" in dim_tables:
        viable = dim_tables["regime"]
        viable_rows = viable[
            viable["profit_factor"].fillna(0) >= 1.0
        ]
        if viable_rows.empty:
            app("  Aucun régime viable détecté — SHORT non déployable en l'état")
        else:
            for _, row in viable_rows.iterrows():
                app(f"  {str(row.iloc[0]):<20s}  PF={row['profit_factor']:.3f}")
    app("")

    app("=" * 72)
    return "\n".join(lines)


# ─── Sauvegarde ───────────────────────────────────────────────────────────────

def save_outputs(
    df_annotated: pd.DataFrame,
    dim_tables: dict[str, pd.DataFrame],
    report_text: str,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = REPORT_DIR / "short_failure_audit.csv"
    df_annotated.to_csv(csv_path, index=False)
    print(f"   CSV sauvegardé : {csv_path}")

    summary: dict = {}
    for dim, tbl in dim_tables.items():
        tbl_clean = tbl.copy()
        for c in tbl_clean.select_dtypes(include=[np.floating]).columns:
            tbl_clean[c] = tbl_clean[c].replace([np.inf, -np.inf], None)
        summary[dim] = tbl_clean.to_dict(orient="records")

    json_path = REPORT_DIR / "short_failure_audit.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"   JSON sauvegardé : {json_path}")

    txt_path = REPORT_DIR / "short_failure_audit.txt"
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(report_text)
    print(f"   Rapport sauvegardé : {txt_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(data_dir: Optional[Path] = None) -> None:
    print("\n[audit_short_failures] Démarrage")

    paths = find_trade_files(data_dir)
    print(f"   {len(paths)} fichier(s) de trades trouvé(s)")

    if paths:
        df_raw = load_trade_files(paths)
        if df_raw is None or df_raw.empty:
            print("   Fichiers chargés mais vides — basculement en mode synthétique")
            df_raw = generate_synthetic_trades()
    else:
        df_raw = generate_synthetic_trades()

    print(f"   Normalisation du DataFrame ({len(df_raw)} lignes)")
    df = normalize_df(df_raw)
    print(f"   Après nettoyage NaN : {len(df)} lignes")

    if len(df) == 0:
        print("   Aucune ligne valide après normalisation — basculement en mode synthétique")
        df = normalize_df(generate_synthetic_trades())

    if "funding_bucket" not in df.columns:
        df["funding_bucket"] = pd.cut(
            df["funding_rate"],
            bins=[-np.inf, -0.0005, 0.0, 0.0005, np.inf],
            labels=["very_negative", "negative", "positive", "very_positive"],
        )

    print("   Classification des trades perdants")
    df["loss_category"] = classify_losses_vectorized(df)

    print("   Analyse par dimension")
    dim_tables = analyse_all_dimensions(df)

    print("   Construction du rapport")
    report_text = build_text_report(df, dim_tables, df["loss_category"])
    print("\n" + report_text)

    print("   Sauvegarde des outputs")
    save_outputs(df, dim_tables, report_text)
    print("[audit_short_failures] Terminé\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit post-mortem des trades SHORT perdants",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Répertoire alternatif de recherche des fichiers de trades",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(data_dir=args.data_dir)
