"""
scripts/propfirm_analysis.py — Tableau de gains par mois, par crypto, par capital PropFirm
==============================================================================================
Lit tous les runs hedge_fund_v4 (et optionnellement v5-v7) et génère :
  - Tableau mensuel par crypto (gains absolus)
  - Simulation sur différentes tailles de compte PropFirm
  - Export HTML + CSV

Usage : python3 scripts/propfirm_analysis.py [--version v4] [--output reports/propfirm.html]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
RUNS_DIR = BASE_DIR / "runs"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Montants PropFirm typiques (USD)
PROPFIRM_CAPITALS = {
    "10k (base)": 10_000,
    "25k": 25_000,
    "50k": 50_000,
    "100k": 100_000,
    "200k": 200_000,
    "500k": 500_000,
}

BASE_CAPITAL = 10_000.0  # capital utilisé dans les backtests


def load_trades(version: str) -> dict[str, pd.DataFrame]:
    """Charge les trades de tous les runs d'une version."""
    version_dir = RUNS_DIR / f"hedge_fund_{version}"
    if not version_dir.exists():
        raise FileNotFoundError(f"Pas de dossier {version_dir}")

    all_trades: dict[str, pd.DataFrame] = {}
    for run_dir in sorted(version_dir.iterdir()):
        trades_path = run_dir / "backtest_long" / "trades.json"
        if not trades_path.exists():
            continue
        trades = json.loads(trades_path.read_text())
        if not trades:
            continue
        df = pd.DataFrame(trades)
        df["date"] = pd.to_datetime(df["date"])
        df["month"] = df["date"].dt.to_period("M")
        # Extraire le nom du crypto depuis le run_id
        crypto = run_dir.name.replace(f"hf_", "").replace(f"_{version}", "").upper()
        crypto = crypto.replace("USDT", "")
        all_trades[crypto] = df

    return all_trades


def compute_monthly_pnl(trades_by_crypto: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Calcule le PnL mensuel agrégé par crypto (base 10k)."""
    records = []
    for crypto, df in trades_by_crypto.items():
        monthly = df.groupby("month").agg(
            trades=("pnl_abs", "count"),
            pnl_usd=("pnl_abs", "sum"),
            win_rate=("pnl_abs", lambda x: (x > 0).mean()),
        ).reset_index()
        monthly["crypto"] = crypto
        records.append(monthly)

    if not records:
        return pd.DataFrame()

    combined = pd.concat(records, ignore_index=True)
    combined["month_str"] = combined["month"].astype(str)
    return combined


def build_propfirm_table(monthly_pnl: pd.DataFrame) -> pd.DataFrame:
    """Construit le tableau complet avec scaling PropFirm."""
    rows = []
    for _, row in monthly_pnl.iterrows():
        base_row = {
            "Mois": row["month_str"],
            "Crypto": row["crypto"],
            "Trades": int(row["trades"]),
            "Win Rate": f"{row['win_rate']:.0%}",
            "PnL% (base)": f"{row['pnl_usd'] / BASE_CAPITAL * 100:.2f}%",
        }
        for label, capital in PROPFIRM_CAPITALS.items():
            scaled = row["pnl_usd"] * capital / BASE_CAPITAL
            base_row[f"${label}"] = f"${scaled:+,.0f}"
        rows.append(base_row)

    df = pd.DataFrame(rows)
    df = df.sort_values(["Mois", "Crypto"]).reset_index(drop=True)
    return df


def build_yearly_summary(trades_by_crypto: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Résumé annuel par crypto avec scaling PropFirm."""
    rows = []
    for crypto, df in trades_by_crypto.items():
        df["year"] = df["date"].dt.year
        yearly = df.groupby("year").agg(
            trades=("pnl_abs", "count"),
            pnl_usd=("pnl_abs", "sum"),
            win_rate=("pnl_abs", lambda x: (x > 0).mean()),
        ).reset_index()
        for _, row in yearly.iterrows():
            base_row = {
                "Année": int(row["year"]),
                "Crypto": crypto,
                "Trades": int(row["trades"]),
                "Win Rate": f"{row['win_rate']:.0%}",
                "PnL% (base)": f"{row['pnl_usd'] / BASE_CAPITAL * 100:.2f}%",
            }
            for label, capital in PROPFIRM_CAPITALS.items():
                scaled = row["pnl_usd"] * capital / BASE_CAPITAL
                base_row[f"${label}"] = f"${scaled:+,.0f}"
            rows.append(base_row)

    df = pd.DataFrame(rows)
    df = df.sort_values(["Année", "Crypto"]).reset_index(drop=True)
    return df


def build_aggregated_monthly(monthly_pnl: pd.DataFrame) -> pd.DataFrame:
    """PnL mensuel agrégé sur toutes les cryptos (portefeuille)."""
    agg = monthly_pnl.groupby("month_str").agg(
        trades=("trades", "sum"),
        pnl_usd=("pnl_usd", "sum"),
    ).reset_index()
    agg["win_pct"] = agg["pnl_usd"].apply(lambda x: "✅" if x > 0 else "❌")
    agg["PnL% (base)"] = (agg["pnl_usd"] / BASE_CAPITAL * 100).map(lambda x: f"{x:.2f}%")
    for label, capital in PROPFIRM_CAPITALS.items():
        agg[f"${label}"] = (agg["pnl_usd"] * capital / BASE_CAPITAL).map(
            lambda x: f"${x:+,.0f}"
        )
    agg = agg.rename(columns={"month_str": "Mois", "trades": "Trades Total", "win_pct": "Positif?"})
    return agg[["Mois", "Trades Total", "Positif?", "PnL% (base)"] + [f"${l}" for l in PROPFIRM_CAPITALS]]


def color_cell(val: str) -> str:
    """Colorie une cellule selon le signe du PnL."""
    if isinstance(val, str):
        if val.startswith("$+") or (val.startswith("$") and "-" not in val and "0" not in val.replace("$","").replace(",","")):
            return "background-color: #d4edda; color: #155724;"
        if "-" in val:
            return "background-color: #f8d7da; color: #721c24;"
    return ""


def to_html(version: str, monthly_detail: pd.DataFrame, monthly_agg: pd.DataFrame, yearly: pd.DataFrame) -> str:
    """Génère le rapport HTML complet."""
    def df_to_html(df: pd.DataFrame, title: str) -> str:
        # Applique coloration sur colonnes PropFirm
        pf_cols = [c for c in df.columns if c.startswith("$")]
        styled = df.style.applymap(color_cell, subset=pf_cols)
        table_html = styled.to_html(index=False, border=0, classes="table")
        return f"<h2>{title}</h2>\n{table_html}\n"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>PropFirm Analysis — Pipeline {version.upper()}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  h1 {{ color: #00d4ff; border-bottom: 2px solid #00d4ff; padding-bottom: 10px; }}
  h2 {{ color: #f0a500; margin-top: 40px; }}
  .table {{ border-collapse: collapse; width: 100%; margin-bottom: 30px; font-size: 13px; }}
  .table th {{ background: #16213e; color: #00d4ff; padding: 8px 12px; text-align: center; border: 1px solid #334; }}
  .table td {{ padding: 6px 12px; border: 1px solid #334; text-align: center; }}
  .table tr:nth-child(even) {{ background: #16213e33; }}
  .summary-box {{ background: #16213e; border: 1px solid #00d4ff33; border-radius: 8px; padding: 15px; margin: 20px 0; }}
  .stat {{ display: inline-block; margin: 10px 20px; }}
  .stat-val {{ font-size: 24px; font-weight: bold; color: #00d4ff; }}
  .stat-lbl {{ font-size: 12px; color: #888; }}
</style>
</head>
<body>
<h1>🏦 PropFirm Analysis — Pipeline Hedge Fund {version.upper()}</h1>
<p style="color:#888;">Généré le {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | Capital base : ${BASE_CAPITAL:,.0f} | Scaling linéaire</p>
<div class="summary-box">
  <div class="stat"><div class="stat-val">{monthly_detail['Trades'].sum()}</div><div class="stat-lbl">Trades total</div></div>
  <div class="stat"><div class="stat-val">{len(monthly_detail['Crypto'].unique())}</div><div class="stat-lbl">Cryptos</div></div>
  <div class="stat"><div class="stat-val">{monthly_detail['Mois'].nunique()}</div><div class="stat-lbl">Mois testés</div></div>
</div>
"""
    html += df_to_html(monthly_agg, "📅 Portefeuille Agrégé — Gains Mensuels (toutes cryptos)")
    html += df_to_html(yearly, "📆 Résumé Annuel par Crypto")
    html += df_to_html(monthly_detail, "🔍 Détail Mensuel par Crypto")
    html += "</body></html>"
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="v4", help="Version hedge fund (v4, v5, v6, v7)")
    parser.add_argument("--output", default=None, help="Fichier HTML de sortie")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  PROPFIRM ANALYSIS — Pipeline Hedge Fund {args.version.upper()}")
    print(f"{'='*70}\n")

    # Charger les données
    trades_by_crypto = load_trades(args.version)
    if not trades_by_crypto:
        print("❌ Aucune donnée de trades trouvée.")
        return

    cryptos = sorted(trades_by_crypto.keys())
    print(f"Cryptos chargées : {', '.join(cryptos)}\n")

    # Calculer les tableaux
    monthly_pnl = compute_monthly_pnl(trades_by_crypto)
    propfirm_table = build_propfirm_table(monthly_pnl)
    yearly_table = build_yearly_summary(trades_by_crypto)
    agg_monthly = build_aggregated_monthly(monthly_pnl)

    # ── Affichage console ────────────────────────────────────────────────────
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)

    print("📅 PORTEFEUILLE AGRÉGÉ — GAINS MENSUELS (toutes cryptos)\n")
    print(agg_monthly.to_string(index=False))

    print("\n\n📆 RÉSUMÉ ANNUEL PAR CRYPTO\n")
    print(yearly_table.to_string(index=False))

    print("\n\n🔍 DÉTAIL MENSUEL PAR CRYPTO\n")
    print(propfirm_table.to_string(index=False))

    # ── Export ───────────────────────────────────────────────────────────────
    out_path = args.output or str(REPORTS_DIR / f"propfirm_{args.version}.html")
    html = to_html(args.version, propfirm_table, agg_monthly, yearly_table)
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"\n\n✅ Rapport HTML exporté : {out_path}")

    # CSV
    csv_path = out_path.replace(".html", "_monthly.csv")
    propfirm_table.to_csv(csv_path, index=False)
    print(f"✅ CSV exporté : {csv_path}")

    # ── Résumé synthétique PropFirm ──────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print(f"  SIMULATION PROPFIRM — GAINS TOTAUX (période complète)")
    print(f"{'='*70}")

    total_pnl_base = monthly_pnl["pnl_usd"].sum()
    print(f"\n  Capital de base (${BASE_CAPITAL:,.0f}) → PnL total : ${total_pnl_base:+,.2f} ({total_pnl_base/BASE_CAPITAL*100:+.2f}%)\n")

    for label, capital in PROPFIRM_CAPITALS.items():
        scaled = total_pnl_base * capital / BASE_CAPITAL
        monthly_avg = scaled / max(1, monthly_pnl["month"].nunique())
        sign = "+" if scaled >= 0 else ""
        print(f"  Compte {label:<12} →  {sign}${abs(scaled):>9,.0f} total  |  ~{sign}${abs(monthly_avg):,.0f}/mois en moyenne")

    print()


if __name__ == "__main__":
    main()
