"""Construit le panel quotidien partagé (V3/V4/V5) depuis les barres 5m data_v2.

Agrégation indépendante : close = dernière barre 5m du jour UTC, dv = somme du
quote_asset_volume, nbar = nombre de barres (diagnostic de trous). Aucune feature
pré-calculée n'est réutilisée.

Sortie : <scratch>/daily_panel.parquet  (symbol, date, close, dv, nbar)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl  # noqa: E402

SCRATCH = os.environ.get(
    "VAL_SCRATCH",
    "/tmp/claude-1000/-home-qbee-futur/96533575-ccfe-4d52-a4ae-a61df9219e6e/scratchpad/validation_wave2",
)
OUT = os.path.join(SCRATCH, "daily_panel.parquet")


def main() -> None:
    os.makedirs(SCRATCH, exist_ok=True)
    t0 = time.time()
    con = vl.duckdb_connect()
    con.execute(
        f"""
        COPY (
            WITH b AS (
                SELECT symbol,
                       CAST(timestamp AS DATE) AS date,
                       close,
                       quote_asset_volume AS qv,
                       timestamp AS ts
                FROM read_parquet('{vl.DEFAULT_PANEL_GLOB}', hive_partitioning=true)
            )
            SELECT symbol,
                   date,
                   arg_max(close, ts) AS close,
                   sum(qv)            AS dv,
                   count(*)           AS nbar
            FROM b
            GROUP BY 1, 2
            ORDER BY 1, 2
        ) TO '{OUT}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    n, nsym, d0, d1 = con.execute(
        f"SELECT count(*), count(DISTINCT symbol), min(date), max(date) FROM read_parquet('{OUT}')"
    ).fetchone()
    con.close()
    print(f"rows={n} symbols={nsym} range={d0}..{d1} "
          f"size={os.path.getsize(OUT)/1e6:.1f}MB elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
