#!/usr/bin/env python3
"""
build_spot_cache.py -- panel SPOT apparie, aligne sur le panel perp.

Sortie : data/_cache/panel_spot_daily.npz  (+ _meta.json)

Ecrit DELIBEREMENT dans un fichier separe. `panel_daily.npz` porte les donnees
sur lesquelles des candidats ont deja ete juges et dont l'empreinte SHA-256 est
au ledger : on n'y touche pas pour ajouter un champ.

Champs, tous alignes sur (index, symboles) du panel perp :
    spot_close        prix spot MULTIPLIE par le facteur du perp (comparable)
    spot_quote_volume volume spot en $
    basis             perp_close / spot_close - 1

Le multiplicateur vient du registre du backfill, jamais devine ici :
`1000PEPEUSDT` vaut 1000 PEPE, comparer les prix bruts donnerait +99 900 %.
"""
import glob, json, os, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/qbee/futur")
CACHE = ROOT / "data" / "_cache"
SPOT = ROOT / "data" / "spot_backfill" / "spot_klines_1d"

meta = json.load(open(CACHE / "panel_daily_meta.json"))
idx = pd.DatetimeIndex(pd.to_datetime(meta["index"], utc=True))
syms = list(meta["symbols"])
z = np.load(CACHE / "panel_daily.npz")
perp_close = pd.DataFrame(z["px_close"], index=idx, columns=syms)
print(f"panel perp : {len(idx)}j x {len(syms)} symboles")

reg = json.load(open(SPOT / "registry.json"))
pairing = reg["pairing"]

sc = pd.DataFrame(np.nan, index=idx, columns=syms, dtype="float64")
sv = pd.DataFrame(np.nan, index=idx, columns=syms, dtype="float64")

cache = {}
n_ok = 0
for perp in syms:
    pr = pairing.get(perp)
    if not pr:
        continue
    spot, mult = pr["spot"], float(pr["mult"])
    if spot not in cache:
        p = SPOT / f"{spot}_1d.parquet"
        if not p.exists():
            cache[spot] = None
        else:
            d = pd.read_parquet(p, columns=["open_time", "close", "quote_volume"])
            t = pd.to_datetime(d["open_time"], utc=True).dt.normalize()
            d = (d.assign(_t=t).drop_duplicates("_t", keep="last")
                   .set_index("_t").sort_index())
            cache[spot] = d
    d = cache[spot]
    if d is None:
        continue
    sc[perp] = pd.to_numeric(d["close"], errors="coerce").reindex(idx) * mult
    sv[perp] = pd.to_numeric(d["quote_volume"], errors="coerce").reindex(idx)
    n_ok += 1

basis = perp_close / sc - 1.0
# Un basis quotidien de perp est de l'ordre de quelques dizaines de bps. Au-dela
# de +-50 %, c'est un appariement faux (multiplicateur, renommage, jeton
# homonyme), pas une prime : on refuse la cellule plutot que de la trader.
insane = basis.abs() > 0.50
basis = basis.mask(insane)

print(f"symboles apparies : {n_ok}   cellules basis : {int(np.isfinite(basis.to_numpy()).sum()):,}")
print(f"cellules rejetees (|basis| > 50 %) : {int(insane.to_numpy().sum()):,}")
b = basis.stack()
print(f"basis : median {b.median()*1e4:+.1f} bps, p5 {b.quantile(.05)*1e4:+.1f}, "
      f"p95 {b.quantile(.95)*1e4:+.1f} bps")

# symboles dont le basis median est aberrant -> appariement suspect
med = basis.median()
susp = sorted(med[(med.abs() > 0.05) & med.notna()].index)
if susp:
    print(f"appariements suspects (|basis median| > 5 %) : {len(susp)} -> {susp[:10]}")

CACHE.mkdir(parents=True, exist_ok=True)
np.savez_compressed(CACHE / "panel_spot_daily.npz",
                    spot_close=sc.to_numpy(dtype="float32"),
                    spot_quote_volume=sv.to_numpy(dtype="float32"),
                    basis=basis.to_numpy(dtype="float32"))
json.dump({"index": [str(t) for t in idx], "symbols": syms,
           "fields": ["spot_close", "spot_quote_volume", "basis"],
           "n_paired": n_ok, "suspect_pairings": susp,
           "source_registry": str(SPOT / "registry.json")},
          open(CACHE / "panel_spot_daily_meta.json", "w"), indent=2)
print(f"-> {CACHE/'panel_spot_daily.npz'}")
