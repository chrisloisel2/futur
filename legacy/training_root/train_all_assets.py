#!/usr/bin/env python3
"""
train_all_assets.py — Entraîne la pipeline complète sur tous les fichiers
*_1h_features.csv disponibles dans data/.

Un run séparé par asset → modèles indépendants + backtests valides par asset.

Usage :
    python train_all_assets.py                          # tous les assets, mode combined
    python train_all_assets.py --mode long              # long uniquement
    python train_all_assets.py --assets BTC ETH SOL     # subset
    python train_all_assets.py --skip-done              # saute les assets déjà traités
    python train_all_assets.py --workers 4              # 4 runs en parallèle
    python train_all_assets.py --detailed-logs logs/diag  # logs mensuels complets par asset

Avec --detailed-logs DIR, chaque asset produit :
    DIR/SYMBOL/YYYY-MM_long.csv     ← trace complète de chaque barre (long)
    DIR/SYMBOL/YYYY-MM_short.csv    ← trace complète de chaque barre (short)
    DIR/SYMBOL/_summary_long.csv    ← résumé mensuel agrégé
    DIR/portfolio_monthly.csv       ← agrégat cross-assets par mois
    DIR/diagnostic_report.txt       ← rapport texte sur les goulots d'étranglement
"""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from data_pipeline.mongo_training import DEFAULT_FEATURE_COLLECTION, build_mongo_training_uri

FUTUR   = Path(__file__).parent
DATA    = FUTUR / "data"
PROFILE = FUTUR / "strategies" / "pipeline_hourly" / "profile.py"
RUNS    = FUTUR / "runs" / "pipeline"


# ─────────────────────────────────────────────────────────────────────────────

def find_enriched_files(assets: list[str] | None = None) -> list[Path]:
    files = sorted(DATA.glob("*_1h_features.csv"))
    if assets:
        assets_upper = [a.upper() for a in assets]
        files = [
            f for f in files
            if any(a in f.stem.upper() for a in assets_upper)
        ]
    return files


def find_mongo_specs(assets: list[str] | None = None) -> list[str]:
    universe = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
    ]
    if assets:
        assets_upper = [a.upper() for a in assets]
        universe = [s for s in universe if any(a in s for a in assets_upper)]
    return [
        build_mongo_training_uri(symbol, interval="1h", collection=DEFAULT_FEATURE_COLLECTION)
        for symbol in universe
    ]


def symbol_from_path(p) -> str:
    if isinstance(p, str) and p.startswith("mongo://"):
        return p.split("mongo://", 1)[1].split("?", 1)[0].replace("/", "")
    return p.stem.replace("_1h_features", "")


def already_trained(symbol: str) -> bool:
    """Retourne True si un run récent (< 7 jours) existe pour ce symbol."""
    matches = sorted(RUNS.glob(f"train_{symbol.lower()}_*"), reverse=True)
    if not matches:
        matches = sorted(RUNS.glob(f"*{symbol.lower()}*"), reverse=True)
    for m in matches[:1]:
        summary = m / "pipeline_summary.json"
        if summary.exists():
            age = time.time() - summary.stat().st_mtime
            if age < 7 * 86400:
                return True
    return False


def run_one(args_tuple) -> dict:
    """Exécute un run complet pour un asset. Appelé dans un sous-processus."""
    path, mode, extra_flags, log_dir = args_tuple
    symbol = symbol_from_path(path)
    run_id = f"train_{symbol.lower()}_{time.strftime('%Y%m%d_%H%M%S')}"
    log_file = log_dir / f"{symbol}.log"

    cmd = [
        sys.executable, str(PROFILE),
        "--data",    str(path),
        "--mode",    mode,
        "--run-id",  run_id,
    ] + extra_flags

    env = os.environ.copy()
    futur_str = str(FUTUR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = futur_str if not existing else f"{futur_str}:{existing}"

    t0 = time.time()
    with open(log_file, "w", encoding="utf-8") as flog:
        proc = subprocess.run(
            cmd,
            stdout=flog,
            stderr=subprocess.STDOUT,
            cwd=futur_str,
            env=env,
        )
    elapsed = time.time() - t0

    result = {
        "symbol":   symbol,
        "run_id":   run_id,
        "success":  proc.returncode == 0,
        "elapsed":  round(elapsed, 1),
        "log":      str(log_file),
    }

    # Récupère les métriques clés depuis pipeline_summary.json
    summary_path = RUNS / run_id / "pipeline_summary.json"
    if summary_path.exists():
        try:
            with open(summary_path) as f:
                s = json.load(f)
            bt_long  = s.get("backtest_long", {})
            bt_short = s.get("backtest_short", {})
            result["long_pf"]    = bt_long.get("profit_factor",      None)
            result["long_wr"]    = bt_long.get("win_rate",            None)
            result["long_ret"]   = bt_long.get("total_return_pct",    None)
            result["short_ok"]   = s.get("short_enabled_for_inference", False)
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTIC PORTFOLIO — agrégation cross-assets des logs mensuels
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_portfolio_logs(diag_dir: Path, results: list[dict]) -> None:
    """
    Après le batch, agrège tous les _summary_long.csv / _summary_short.csv
    en un fichier portfolio_monthly.csv et génère un rapport de diagnostic.

    Colonnes du portfolio_monthly.csv :
      month, side, n_assets,
      n_bars_total, n_traded_total, n_filter, n_gate, n_bear_regime, n_direction, n_risk,
      pf_mean, pf_median, wr_mean, pnl_sum,
      p_filter_mean, p_dir_mean, p_dir_traded_mean,
      assets_positive, assets_negative

    Le rapport diagnostic_report.txt identifie :
      - Le goulot d'étranglement principal (quel filtre bloque le plus de trades)
      - Les mois les plus profitables / déficitaires
      - Les assets avec la meilleure et la pire alpha
      - Les signaux les plus corrélés avec les trades gagnants
    """
    portfolio_rows = []
    by_month: dict[str, dict] = defaultdict(lambda: {
        "sides":       defaultdict(list),   # side → list of monthly rows
        "n_assets":    0,
    })

    ok_symbols = {r["symbol"] for r in results if r.get("success")}

    for symbol_dir in diag_dir.iterdir():
        if not symbol_dir.is_dir() or symbol_dir.name.startswith("_"):
            continue
        symbol = symbol_dir.name
        if symbol not in ok_symbols:
            continue

        for side in ("long", "short"):
            summary_file = symbol_dir / f"_summary_{side}.csv"
            if not summary_file.exists():
                continue
            with open(summary_file, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    month = row["month"]
                    by_month[month]["sides"][side].append({
                        "symbol":        symbol,
                        "n_bars":        int(row.get("n_bars",        0)),
                        "n_traded":      int(row.get("n_traded",      0)),
                        "n_filter":      int(row.get("n_filter",      0)),
                        "n_gate":        int(row.get("n_gate",        0)),
                        "n_bear_regime": int(row.get("n_bear_regime", 0)),
                        "n_direction":   int(row.get("n_direction",   0)),
                        "n_risk":        int(row.get("n_risk",        0)),
                        "pf":            float(row.get("pf",    0) or 0),
                        "wr":            float(row.get("wr",    0) or 0),
                        "pnl_sum":       float(row.get("pnl_sum", 0) or 0),
                        "p_filter_mean": float(row.get("p_filter_mean", 0) or 0),
                        "p_dir_mean":    float(row.get("p_dir_mean",    0) or 0),
                        "p_dir_traded":  float(row.get("p_dir_traded",  0) or 0),
                    })

    for month, data in sorted(by_month.items()):
        for side, rows in data["sides"].items():
            if not rows:
                continue
            n_assets  = len(rows)
            pfs       = [r["pf"] for r in rows if r["pf"] > 0]
            pf_mean   = sum(pfs) / len(pfs) if pfs else 0.0
            pf_med    = sorted(pfs)[len(pfs) // 2] if pfs else 0.0
            portfolio_rows.append({
                "month":           month,
                "side":            side,
                "n_assets":        n_assets,
                "n_bars_total":    sum(r["n_bars"]        for r in rows),
                "n_traded_total":  sum(r["n_traded"]      for r in rows),
                "n_filter":        sum(r["n_filter"]      for r in rows),
                "n_gate":          sum(r["n_gate"]        for r in rows),
                "n_bear_regime":   sum(r["n_bear_regime"] for r in rows),
                "n_direction":     sum(r["n_direction"]   for r in rows),
                "n_risk":          sum(r["n_risk"]        for r in rows),
                "pf_mean":         round(pf_mean, 4),
                "pf_median":       round(pf_med,  4),
                "wr_mean":         round(sum(r["wr"] for r in rows) / n_assets, 4),
                "pnl_sum":         round(sum(r["pnl_sum"] for r in rows), 4),
                "p_filter_mean":   round(sum(r["p_filter_mean"] for r in rows) / n_assets, 4),
                "p_dir_mean":      round(sum(r["p_dir_mean"]    for r in rows) / n_assets, 4),
                "p_dir_traded":    round(sum(r["p_dir_traded"]  for r in rows) / n_assets, 4) if any(r["p_dir_traded"] for r in rows) else 0.0,
                "assets_positive": sum(1 for r in rows if r["pnl_sum"] > 0),
                "assets_negative": sum(1 for r in rows if r["pnl_sum"] < 0),
            })

    if portfolio_rows:
        out_csv = diag_dir / "portfolio_monthly.csv"
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(portfolio_rows[0].keys()))
            writer.writeheader()
            writer.writerows(portfolio_rows)
        print(f"\n  Portfolio mensuel   : {out_csv}")

        # Rapport diagnostic textuel
        _write_diagnostic_report(diag_dir, portfolio_rows, results)


def _write_diagnostic_report(
    diag_dir: Path,
    portfolio_rows: list[dict],
    results: list[dict],
) -> None:
    """Génère un rapport lisible identifiant les goulots d'étranglement."""
    lines = [
        "=" * 72,
        "  RAPPORT DIAGNOSTIC PIPELINE — ANALYSE ALPHA",
        f"  Généré le : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 72,
        "",
    ]

    long_rows = [r for r in portfolio_rows if r["side"] == "long"]

    if long_rows:
        # ── 1. Goulot d'étranglement principal ───────────────────────────────
        total_filter    = sum(r["n_filter"]      for r in long_rows)
        total_gate      = sum(r["n_gate"]        for r in long_rows)
        total_direction = sum(r["n_direction"]   for r in long_rows)
        total_risk      = sum(r["n_risk"]        for r in long_rows)
        total_traded    = sum(r["n_traded_total"] for r in long_rows)
        total_bars      = sum(r["n_bars_total"]  for r in long_rows)

        total_skip = total_filter + total_gate + total_direction + total_risk
        lines += [
            "  1. GOULOTS D'ÉTRANGLEMENT (LONG — test period)",
            "  " + "─" * 68,
            f"  Barres testées      : {total_bars:,}",
            f"  Trades exécutés     : {total_traded:,}  ({total_traded/max(total_bars,1):.2%})",
            f"  Skipped par filtre  : {total_filter:,}  ({total_filter/max(total_bars,1):.1%})  ← filtre tradeable",
            f"  Skipped par gate    : {total_gate:,}  ({total_gate/max(total_bars,1):.1%})  ← EMA+ADX+ST+Ichi",
            f"  Skipped par direct. : {total_direction:,}  ({total_direction/max(total_bars,1):.1%})  ← modèle directionnel",
            f"  Skipped par risque  : {total_risk:,}  ({total_risk/max(total_bars,1):.1%})  ← RiskController",
            "",
            "  ⟹  Lever actionnable :",
        ]
        dominant = max(
            [("filtre",     total_filter),
             ("gate",       total_gate),
             ("direction",  total_direction)],
            key=lambda x: x[1]
        )
        lines.append(f"     Le '{dominant[0]}' bloque le plus ({dominant[1]:,} barres).")
        if dominant[0] == "filtre":
            lines.append("     → Baisser filter_threshold_long (ex: 0.35) pour voir plus de barres")
        elif dominant[0] == "gate":
            lines.append("     → Assouplir indicator_gate_min_score (ex: 1) ou retirer 1 condition")
        elif dominant[0] == "direction":
            lines.append("     → Baisser direction_threshold_long (ex: 0.54) ou améliorer AUC modèle")
        lines.append("")

        # ── 2. Mois les plus profitables ─────────────────────────────────────
        sorted_by_pnl = sorted(long_rows, key=lambda r: r["pnl_sum"], reverse=True)
        lines += [
            "  2. TOP 5 MOIS LONG (par PnL agrégé cross-assets)",
            "  " + "─" * 68,
            f"  {'Mois':>8}  {'Assets':>6}  {'PnL':>9}  {'PF moyen':>9}  {'WR moyen':>9}  {'Tradés':>7}",
        ]
        for r in sorted_by_pnl[:5]:
            lines.append(
                f"  {r['month']:>8}  {r['n_assets']:>6}  "
                f"{r['pnl_sum']:>+9.2f}  {r['pf_mean']:>9.3f}  "
                f"{r['wr_mean']:>8.1%}  {r['n_traded_total']:>7}"
            )
        lines.append("")
        lines += [
            "  BOTTOM 5 MOIS LONG (pires mois)",
            "  " + "─" * 68,
        ]
        for r in sorted_by_pnl[-5:]:
            lines.append(
                f"  {r['month']:>8}  {r['n_assets']:>6}  "
                f"{r['pnl_sum']:>+9.2f}  {r['pf_mean']:>9.3f}  "
                f"{r['wr_mean']:>8.1%}  {r['n_traded_total']:>7}"
            )
        lines.append("")

        # ── 3. Calibration des modèles ────────────────────────────────────────
        p_filter_all = [r["p_filter_mean"] for r in long_rows if r["p_filter_mean"] > 0]
        p_dir_all    = [r["p_dir_mean"]    for r in long_rows if r["p_dir_mean"] > 0]
        p_dir_tr     = [r["p_dir_traded"]  for r in long_rows if r["p_dir_traded"] > 0]
        lines += [
            "  3. CALIBRATION DES MODÈLES",
            "  " + "─" * 68,
            f"  p_filter moyen (toutes barres) : {sum(p_filter_all)/max(len(p_filter_all),1):.4f}",
            f"  p_direction moyen (toutes)     : {sum(p_dir_all)/max(len(p_dir_all),1):.4f}",
            f"  p_direction moyen (tradés)     : {sum(p_dir_tr)/max(len(p_dir_tr),1):.4f}",
            "",
            "  ⟹  Si p_direction_tradés ≈ p_direction_toutes : le modèle discrimine peu",
            "      → Améliorer AUC via plus de features ou meilleure régularisation",
            "  ⟹  Si p_filter_moyen faible (<0.35) : le filtre rejette trop",
            "      → Baisser le seuil ou ré-entraîner le filtre avec plus de données",
            "",
        ]

    # ── 4. TOP/BOTTOM assets ──────────────────────────────────────────────────
    ok_results = [r for r in results if r.get("success") and r.get("long_pf") is not None]
    if ok_results:
        ranked = sorted(ok_results, key=lambda r: r["long_pf"] or 0, reverse=True)
        lines += [
            "  4. CLASSEMENT ASSETS (PF long — test complet)",
            "  " + "─" * 68,
            f"  {'Asset':>14}  {'PF':>7}  {'WR':>7}  {'Return':>8}",
        ]
        for r in ranked[:10]:
            pf_str = f"{r['long_pf']:.3f}" if r.get("long_pf") else "   inf"
            wr_str = f"{r.get('long_wr', 0):.0%}" if r.get("long_wr") is not None else "   -"
            rt_str = f"{r.get('long_ret', 0):+.1f}%" if r.get("long_ret") is not None else "   -"
            lines.append(f"  {r['symbol']:>14}  {pf_str:>7}  {wr_str:>7}  {rt_str:>8}")
        lines += [
            "  ...",
            "  Pires assets :",
        ]
        for r in ranked[-5:]:
            pf_str = f"{r['long_pf']:.3f}" if r.get("long_pf") else "   0"
            lines.append(f"  {r['symbol']:>14}  {pf_str:>7}")
        lines.append("")

    lines += [
        "=" * 72,
        "  RECOMMANDATIONS POUR AMÉLIORER L'ALPHA",
        "=" * 72,
        "",
        "  A) COURT TERME (sans ré-entraînement)",
        "     • Augmenter risk_per_trade de 0.2% → 0.5% sur assets PF > 1.2",
        "     • Désactiver le SHORT si PF short < 0.95 (ajout --no-short)",
        "     • Relever direction_thr_long à 0.58 sur assets avec WR < 45%",
        "",
        "  B) MOYEN TERME (ré-entraînement ciblé)",
        "     • Ajouter plus de données récentes (2024-2026) au training set",
        "     • Activer walk-forward training pour capturer les régimes récents",
        "     • Améliorer AUC via feature selection (garder top-30 par importance)",
        "",
        "  C) LONG TERME (architecture)",
        "     • Spécialistes par régime de marché (bull/bear/range séparément)",
        "     • Calibration mensuelle des seuils (rolling threshold update)",
        "     • Ensemble cross-assets (signal BTC gate les altcoins)",
        "",
    ]

    report_path = diag_dir / "diagnostic_report.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Rapport diagnostic  : {report_path}")


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Entraîne la pipeline sur tous les assets")
    ap.add_argument("--assets", nargs="+", default=None,
                    help="Subset d'assets à entraîner (ex: BTC ETH SOL)")
    ap.add_argument("--mode",  choices=["long", "short", "combined"],
                    default="combined",
                    help="Branche(s) à entraîner (défaut: combined)")
    ap.add_argument("--workers", type=int, default=1,
                    help="Nombre de runs parallèles (défaut: 1 = séquentiel)")
    ap.add_argument("--skip-done", action="store_true",
                    help="Saute les assets déjà entraînés récemment (< 7 j)")
    ap.add_argument("--skip-tcn", action="store_true",
                    help="Saute l'entraînement TCN pour aller plus vite")
    ap.add_argument("--detailed-logs", type=str, default=None, metavar="DIR",
                    help=(
                        "Active les logs mensuels détaillés. Produit pour chaque asset : "
                        "YYYY-MM_long.csv / YYYY-MM_short.csv avec la trace complète "
                        "(p_filter, gate, p_direction, p_bear, skip_reason, pnl, equity). "
                        "Génère aussi portfolio_monthly.csv et diagnostic_report.txt."
                    ))
    ap.add_argument("--source", choices=["mongo", "files"], default="mongo",
                    help="Source des datasets: collection Mongo enrichie ou fichiers data/")
    args = ap.parse_args()

    files = find_mongo_specs(args.assets) if args.source == "mongo" else find_enriched_files(args.assets)
    if not files:
        print("Aucun dataset trouvé")
        sys.exit(1)

    if args.skip_done:
        before = len(files)
        files = [f for f in files if not already_trained(symbol_from_path(f))]
        print(f"--skip-done : {before - len(files)} assets ignorés (déjà traités)")

    # Prépare les flags extra à passer à profile.py
    extra_flags = []
    if args.skip_tcn:
        extra_flags.append("--skip-tcn")

    diag_dir: Path | None = None
    if args.detailed_logs:
        diag_dir = Path(args.detailed_logs)
        diag_dir.mkdir(parents=True, exist_ok=True)
        extra_flags += ["--detailed-logs", str(diag_dir)]
        print(f"\n  Logs détaillés activés → {diag_dir}")

    log_dir = FUTUR / "runs" / "batch_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)

    n = len(files)
    print(f"\n{'='*68}")
    print(f"  BATCH TRAINING — {n} assets  |  mode={args.mode}  |  workers={args.workers}")
    if diag_dir:
        print(f"  Diagnostic logs   → {diag_dir}")
    print(f"{'='*68}")
    for f in files:
        print(f"  {symbol_from_path(f)}")
    print()

    tasks = [(f, args.mode, extra_flags, log_dir) for f in files]
    results = []
    t_batch = time.time()

    if args.workers <= 1:
        for i, task in enumerate(tasks, 1):
            sym = symbol_from_path(task[0])
            print(f"[{i:2d}/{n}] {sym:14s} ...", end=" ", flush=True)
            r = run_one(task)
            _print_result(r)
            results.append(r)
    else:
        workers = min(args.workers, n)
        print(f"Lancement de {workers} workers en parallèle…\n")
        with multiprocessing.Pool(workers) as pool:
            for i, r in enumerate(pool.imap_unordered(run_one, tasks), 1):
                print(f"[{i:2d}/{n}] ", end="")
                _print_result(r)
                results.append(r)

    elapsed_total = time.time() - t_batch
    _print_summary(results, elapsed_total)

    # Sauvegarde du rapport batch
    report_path = log_dir / f"batch_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nRapport JSON : {report_path}")

    # Agrégation des logs diagnostics si demandé
    if diag_dir is not None:
        print(f"\n{'='*68}")
        print("  AGRÉGATION DES LOGS DIAGNOSTICS")
        print(f"{'='*68}")
        try:
            _aggregate_portfolio_logs(diag_dir, results)
        except Exception as exc:
            print(f"  ⚠  Agrégation échouée : {exc}")


def _print_result(r: dict) -> None:
    status = "✓" if r["success"] else "✗"
    pf_str = ""
    if r.get("long_pf") is not None:
        pf_str = f"  PF={r['long_pf']:.2f}  WR={r.get('long_wr', 0):.0%}  ret={r.get('long_ret', 0):+.1f}%"
        if r.get("short_ok"):
            pf_str += "  short=OK"
    print(f"{status} {r['symbol']:14s}  {r['elapsed']:5.1f}s{pf_str}")
    if not r["success"]:
        print(f"   └ log: {r['log']}")


def _print_summary(results: list[dict], elapsed: float) -> None:
    ok       = [r for r in results if r["success"]]
    failed   = [r for r in results if not r["success"]]
    short_ok = [r for r in ok if r.get("short_ok")]

    print(f"\n{'='*68}")
    print(f"  RÉSUMÉ BATCH")
    print(f"{'='*68}")
    print(f"  Assets traités  : {len(results)}")
    print(f"  Succès          : {len(ok)}")
    print(f"  Échecs          : {len(failed)}")
    print(f"  Short déployé   : {len(short_ok)}/{len(ok)}")
    print(f"  Durée totale    : {elapsed/60:.1f} min")

    if ok:
        pfs = [r["long_pf"] for r in ok if r.get("long_pf") is not None]
        if pfs:
            finite = [p for p in pfs if p != float("inf")]
            print(f"\n  PF long moyen   : {sum(finite)/max(len(finite),1):.3f}  (excl. inf)")
            print(f"  PF long max     : {max(pfs):.3f}  ({ok[[r['long_pf'] for r in ok].index(max(pfs))]['symbol']})")
            print(f"  PF > 1 (edge)   : {sum(1 for p in pfs if p > 1)} assets")

    if failed:
        print(f"\n  Échecs : {[r['symbol'] for r in failed]}")
        for r in failed:
            print(f"    {r['symbol']} → {r['log']}")

    if ok:
        print(f"\n  TOP 10 (PF long) :")
        ranked = sorted([r for r in ok if r.get("long_pf")],
                        key=lambda r: r["long_pf"] or 0, reverse=True)
        for r in ranked[:10]:
            short_tag = " [SHORT OK]" if r.get("short_ok") else ""
            pf_disp = f"{r['long_pf']:.3f}" if r['long_pf'] != float("inf") else "   ∞  "
            print(f"    {r['symbol']:14s}  PF={pf_disp}  "
                  f"WR={r.get('long_wr',0):.0%}  "
                  f"ret={r.get('long_ret',0):+.1f}%{short_tag}")


if __name__ == "__main__":
    main()
