#!/usr/bin/env python3
"""
scripts/fetch_hyperliquid_tick_archive.py
─────────────────────────────────────────────────────────────────────────────
Achat PLAFONNÉ de l'archive tick Hyperliquid (S3 requester-pays).

Décision utilisateur 2026-07-18 : acheter l'historique fin avec un plafond de
coût faible plutôt qu'attendre un collecteur live. Le 1h est définitivement
rejeté (Binance mène, le premium HL suit — scan 2026-07-17).

Bucket public requester-pays : s3://hyperliquid-archive (ap-northeast-1)
  market_data/{YYYYMMDD}/{H}/l2Book/{COIN}.lz4   (snapshots L2, ~1/s)
  asset_ctxs/{YYYYMMDD}.csv.lz4                  (mark/oracle/funding par bloc)

Sécurités :
  1. ESTIMATION d'abord : HEAD d'un échantillon, extrapolation, affichage du
     coût projeté (transfert ~0.12 $/GB Tokyo + GET). Sans --confirm : dry-run.
  2. PLAFOND DUR --max-cost-usd (défaut 5 $) : le téléchargement s'arrête net
     si le cumul réel projeté dépasse le plafond.
  3. Reprise idempotente : les fichiers déjà présents sont sautés.

Prérequis : `aws configure` avec des credentials VALIDES (2026-07-18 : token
local invalide — à rafraîchir avant usage).

    .venv/bin/python scripts/fetch_hyperliquid_tick_archive.py \
        --coins BTC,ETH,SOL --start 2026-06-01 --end 2026-06-30 \
        --streams l2Book,asset_ctxs --max-cost-usd 5 [--confirm]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "hyperliquid-archive"
REGION = "ap-northeast-1"
OUT = ROOT / "data" / "hl_tick_archive"
USD_PER_GB = 0.12               # transfert sortant Tokyo, arrondi haut
USD_PER_1K_GET = 0.00037


def aws(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--region", REGION, "--request-payer", "requester"],
                          capture_output=True, text=True, timeout=120)


def head_size(key: str) -> int | None:
    r = aws("s3api", "head-object", "--bucket", BUCKET, "--key", key)
    if r.returncode != 0:
        return None
    return int(json.loads(r.stdout)["ContentLength"])


def keys_for(day: pd.Timestamp, coins: list[str], streams: list[str]) -> list[str]:
    d = day.strftime("%Y%m%d")
    out = []
    if "l2Book" in streams:
        out += [f"market_data/{d}/{h}/l2Book/{c}.lz4" for h in range(24) for c in coins]
    if "asset_ctxs" in streams:
        out.append(f"asset_ctxs/{d}.csv.lz4")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="BTC,ETH,SOL")
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--streams", default="l2Book,asset_ctxs")
    ap.add_argument("--max-cost-usd", type=float, default=5.0)
    ap.add_argument("--confirm", action="store_true", help="télécharge (sinon dry-run)")
    args = ap.parse_args()
    coins = [c.strip() for c in args.coins.split(",")]
    streams = [s.strip() for s in args.streams.split(",")]
    days = pd.date_range(args.start, args.end, freq="D")

    # credentials valides ?
    r = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True)
    if r.returncode != 0:
        print("✗ Credentials AWS invalides — lancer `aws configure` d'abord.")
        print(r.stderr.strip()[:200])
        sys.exit(1)

    # 1. estimation sur le premier jour, extrapolée
    sample = keys_for(days[0], coins, streams)
    sizes = [s for k in sample if (s := head_size(k)) is not None]
    if not sizes:
        print(f"✗ Aucun objet trouvé pour {days[0].date()} — vérifier layout/permissions.")
        sys.exit(1)
    day_gb = sum(sizes) / 1e9
    n_keys_total = len(sample) * len(days)
    est_gb = day_gb * len(days)
    est_usd = est_gb * USD_PER_GB + n_keys_total / 1000 * USD_PER_1K_GET
    print(f"Échantillon {days[0].date()} : {len(sizes)}/{len(sample)} objets, {day_gb:.2f} GB")
    print(f"Estimation {len(days)} jours × {len(coins)} coins : {est_gb:.1f} GB "
          f"≈ {est_usd:.2f} $ (plafond {args.max_cost_usd:.2f} $)")
    if est_usd > args.max_cost_usd:
        print("✗ ESTIMATION AU-DESSUS DU PLAFOND — réduire jours/coins/streams.")
        sys.exit(2)
    if not args.confirm:
        print("Dry-run terminé (relancer avec --confirm pour télécharger).")
        return

    # 2. téléchargement sous plafond dur
    got_bytes, skipped, failed = 0, 0, 0
    for day in days:
        for key in keys_for(day, coins, streams):
            dest = OUT / key
            if dest.exists():
                skipped += 1
                continue
            cost_now = got_bytes / 1e9 * USD_PER_GB
            if cost_now >= args.max_cost_usd:
                print(f"✗ PLAFOND ATTEINT ({cost_now:.2f} $) — arrêt propre à {key}")
                sys.exit(3)
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = aws("s3api", "get-object", "--bucket", BUCKET, "--key", key, str(dest))
            if r.returncode != 0:
                failed += 1
                continue
            got_bytes += dest.stat().st_size
    print(f"✓ {got_bytes/1e9:.2f} GB téléchargés ({got_bytes/1e9*USD_PER_GB:.2f} $), "
          f"{skipped} déjà présents, {failed} échecs → {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
