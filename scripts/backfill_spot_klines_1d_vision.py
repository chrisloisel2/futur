#!/usr/bin/env python3
"""
scripts/backfill_spot_klines_1d_vision.py
─────────────────────────────────────────────────────────────────────────────
Backfill klines 1d SPOT Binance (Vision, mensuel), apparié aux perps USDT-M.

Source : https://data.binance.vision/data/spot/monthly/klines/
         {SYM}/1d/{SYM}-1d-{YYYY-MM}.zip

Pourquoi. Le panel ne contient que des perps : aucune famille « basis » n'est
donc calculable, alors que c'est la seule prime du crypto dont le payeur est
explicite (le long à levier paie le cash-and-carry). Le spot apparié débloque
le basis et, avec `binance_vision_quarterly` déjà sur disque, sa structure par
terme.

L'APPARIEMENT N'EST PAS L'IDENTITÉ. Beaucoup de perps cotent un multiple du
jeton pour garder un tick utile : `1000PEPEUSDT` vaut 1000 PEPE, et son spot est
`PEPEUSDT`. Comparer les deux prix bruts donnerait un basis de +99 900 %. Le
multiplicateur est donc explicite, porté dans le registre, et appliqué au
moment de l'appariement — jamais deviné plus tard.

Idempotent : manifest par symbole (mois faits / 404). Sortie :
  data/spot_backfill/spot_klines_1d/{SYM}_1d.parquet

Usage :
  python3 scripts/backfill_spot_klines_1d_vision.py --symbols-file univ.txt
  python3 scripts/backfill_spot_klines_1d_vision.py --symbols BTCUSDT,1000PEPEUSDT
"""
from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "spot_backfill" / "spot_klines_1d"
BASE = "https://data.binance.vision/data/spot/monthly/klines"

KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume",
         "ignore"]

_MULT = re.compile(r"^(1000+)([A-Z0-9]+USDT)$")


def spot_pair(perp: str) -> tuple[str, float]:
    """(symbole spot, multiplicateur) tel que  prix_perp ≈ mult × prix_spot.

    `1000PEPEUSDT` -> (`PEPEUSDT`, 1000.0). Le multiplicateur est le facteur par
    lequel il faut MULTIPLIER le prix spot pour le rendre comparable au perp."""
    m = _MULT.match(perp)
    if m:
        return m.group(2), float(m.group(1))
    return perp, 1.0


def _shift_months(d: date, k: int) -> date:
    """La date decalee de k mois, ramenee au 1er."""
    m = d.month - 1 + k
    return date(d.year + m // 12, m % 12 + 1, 1)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _fetch_month(sym: str, ym: str):
    url = f"{BASE}/{sym}/1d/{sym}-1d-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return ym, ("404" if e.code == 404 else f"http_{e.code}"), None
    except Exception as e:
        return ym, f"err_{type(e).__name__}", None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if isinstance(df.iloc[0, 0], str) and not str(df.iloc[0, 0]).isdigit():
            df = df.iloc[1:].reset_index(drop=True)
        df.columns = KCOLS[: len(df.columns)]
        return ym, "ok", df
    except Exception as e:
        return ym, f"parse_{type(e).__name__}", None


def backfill_symbol(sym: str, start: date, end: date, workers: int = 6,
                    retry_recent: int = 3) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pq = OUT_DIR / f"{sym}_1d.parquet"
    mf = OUT_DIR / f"{sym}_manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else {}
    manifest.setdefault("done", [])
    # TROIS ETATS, PAS DEUX. La classe de bug est : « absent au moment de
    # l'interrogation » enregistre comme « absent definitivement ». Toute source
    # publiee avec retard, interrogee trop tot, cree alors un trou PERMANENT --
    # et toujours au bord d'attaque, la ou s'accumule le seul actif renouvelable.
    # Constate le 2026-09-09 : tout juillet 2026 manquait sur les 787 symboles
    # alors que l'archive existait (HTTP 200 verifie).
    #
    #   done    : recupere
    #   absent  : 404 ANTERIEUR au dernier mois recupere -> le symbole n'etait
    #             pas liste a cette date. Definitif, jamais redemande.
    #   pending : 404 POSTERIEUR au dernier mois recupere -> indistinguable d'un
    #             retard de publication. TOUJOURS redemande.
    #
    # L'ancien format a deux etats est migre en placant tout `missing` en
    # `pending` : on prefere re-interroger pour rien plutot que garder un trou.
    if "missing" in manifest:
        manifest.setdefault("pending", [])
        manifest["pending"] = sorted(set(manifest["pending"]) | set(manifest.pop("missing")))
    manifest.setdefault("absent", [])
    manifest.setdefault("pending", [])
    # filet de securite : les `retry_recent` derniers mois sont toujours repris,
    # meme s'ils ont ete classes `absent` par une version anterieure.
    recents = set(_months(_shift_months(end, -retry_recent), end))
    manifest["absent"] = sorted(set(manifest["absent"]) - recents)
    skip = set(manifest["done"]) | set(manifest["absent"])
    todo = [ym for ym in _months(start, end) if ym not in skip]
    if not todo:
        return {"symbol": sym, "new": 0, "status": "up_to_date"}

    frames, quatre04, nerr = [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_month, sym, ym): ym for ym in todo}
        for fut in as_completed(futs):
            ym, status, df = fut.result()
            if status == "ok":
                frames.append(df)
                manifest["done"].append(ym)
            elif status == "404":
                quatre04.append(ym)
            else:
                nerr += 1
    # classification des 404, une fois le dernier mois recupere connu
    dernier = max(manifest["done"]) if manifest["done"] else ""
    for ym in quatre04:
        (manifest["absent"] if ym < dernier else manifest["pending"]).append(ym)
    n404 = len(quatre04)

    if frames:
        new = pd.concat(frames, ignore_index=True)
        for c in new.columns:
            if c != "ignore":
                new[c] = pd.to_numeric(new[c], errors="coerce")
        # Binance a bascule les horodatages de la milliseconde a la MICROseconde
        # en cours d'historique. Un lot mensuel concatene peut donc melanger les
        # deux unites, et deduire UNE unite pour tout le lot depuis `iloc[-1]`
        # (derniere ligne d'un concat non trie, donc quelconque) date les lignes
        # de l'autre unite a l'an 57056. On tranche LIGNE PAR LIGNE.
        ot = pd.to_numeric(new["open_time"], errors="coerce")
        ot = ot.where(ot < 1e14, ot / 1000.0)          # us -> ms
        new["open_time"] = pd.to_datetime(ot, unit="ms", utc=True)
        new = new.drop(columns=["close_time", "ignore"], errors="ignore")
        if pq.exists():
            old = pd.read_parquet(pq)
            new = pd.concat([old, new], ignore_index=True)
        new = (new.drop_duplicates(subset=["open_time"])
                  .sort_values("open_time").reset_index(drop=True))
        tmp = pq.with_suffix(".tmp.parquet")
        new.to_parquet(tmp, index=False)
        tmp.replace(pq)
        rows = len(new)
    else:
        rows = 0

    manifest["done"] = sorted(set(manifest["done"]))
    manifest["absent"] = sorted(set(manifest["absent"]) - set(manifest["done"]))
    manifest["pending"] = sorted(set(manifest["pending"]) - set(manifest["done"])
                                 - set(manifest["absent"]))
    mf.write_text(json.dumps(manifest))
    return {"symbol": sym, "new": len(frames), "n404": n404, "errors": nerr,
            "pending": len(manifest["pending"]), "rows_total": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="symboles PERP ; l'appariement est fait ici")
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument("--start", default="2019-09-01")
    ap.add_argument("--end", default=None, help="défaut : mois courant")
    ap.add_argument("--retry-recent", type=int, default=3,
                    help="mois recents dont le 404 est re-essaye (retard de publication)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--par-symbols", type=int, default=6)
    args = ap.parse_args()

    if args.symbols_file:
        perps = [s.strip() for s in Path(args.symbols_file).read_text().split() if s.strip()]
    elif args.symbols:
        perps = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        raise SystemExit("--symbols ou --symbols-file requis")

    pairing = {p: spot_pair(p) for p in perps}
    # un meme spot peut servir plusieurs perps : on ne telecharge qu'une fois
    to_fetch = sorted({s for s, _ in pairing.values()})
    n_mult = sum(1 for _, m in pairing.values() if m != 1.0)

    start = date.fromisoformat(args.start + ("-01" if len(args.start) == 7 else ""))
    today = date.today()
    end = (date.fromisoformat(args.end + ("-01" if len(args.end) == 7 else ""))
           if args.end else date(today.year, today.month, 1))

    print(f"Backfill klines 1d SPOT : {len(perps)} perps -> {len(to_fetch)} spots "
          f"({n_mult} avec multiplicateur), {start} → {end}", flush=True)
    t0 = time.time()
    reg = {}
    with ThreadPoolExecutor(max_workers=args.par_symbols) as ex:
        futs = {ex.submit(backfill_symbol, s, start, end, args.workers, args.retry_recent): s
                for s in to_fetch}
        for i, fut in enumerate(as_completed(futs)):
            r = fut.result()
            reg[r["symbol"]] = r
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(to_fetch)}] {r['symbol']:18} "
                      f"new={r.get('new',0):3} rows={r.get('rows_total','-')}", flush=True)

    absent = sorted(s for s in to_fetch if not reg.get(s, {}).get("rows_total"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "registry.json").write_text(json.dumps(
        {"generated_at": pd.Timestamp.utcnow().isoformat(),
         "window": [str(start), str(end)],
         "pairing": {p: {"spot": s, "mult": m} for p, (s, m) in pairing.items()},
         "spot_absent": absent,
         "symbols": reg}, indent=2))
    print(f"\n{len(to_fetch)-len(absent)}/{len(to_fetch)} spots récupérés, "
          f"{len(absent)} sans spot. Terminé en {time.time()-t0:.0f}s → {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
