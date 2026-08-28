set -euo pipefail

cd /home/qbee/futur-data-v2

# ============================================================
# POST-PROCESSING DES 6H DEJA COLLECTEES
# AUCUNE NOUVELLE COLLECTE
# ============================================================

git pull --ff-only origin research/market-physics-data-v3

git rev-parse HEAD

# Attendu :
# 930b6dd4ad72cfee132f65f4be359a6a95fa5d53


# ------------------------------------------------------------
# 1. Tester UNIQUEMENT le correctif streaming
# Il doit collecter 5 tests.
# ------------------------------------------------------------

/home/qbee/futur/.venv/bin/python3 -m pytest \
  tests/unit/test_market_physics_streaming_v3.py \
  -v


# ------------------------------------------------------------
# 2. Vérifier que les health 6h sont toujours présents
# ------------------------------------------------------------

/home/qbee/futur/.venv/bin/python3 - <<'PY'
import json

for venue in ["binance", "bybit", "okx", "hyperliquid"]:
    p = f"reports/market_physics_v3/health/{venue}.json"
    d = json.load(open(p))

    duration = (d["stopped_ns"] - d["started_ns"]) / 1e9

    print(
        venue,
        "duration_s=", round(duration, 3),
        "clean=", d["clean_shutdown"],
        "events=", d["events"],
        "parse=", d["parse_errors"],
        "gaps=", d["sequence_gaps"],
        "reconnects=", d["reconnects"],
    )
PY


# ------------------------------------------------------------
# 3. Construire le state tape 100 ms
#
# IMPORTANT :
# Cette version affiche maintenant sa progression :
#
# [state-tape] validate receive-order ...
# [state-tape] ... ordered -> direct replay
#
# ou éventuellement :
#
# [state-tape] ... inversion detected -> external reorder
#
# puis :
#
# [state-tape] wrote part-00000.parquet ...
# ------------------------------------------------------------

/home/qbee/futur/.venv/bin/python3 \
  scripts/build_market_physics_state_tape_stream_v3.py \
  --venues binance,bybit,okx,hyperliquid \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --cadence-ms 100 \
  --chunk-rows 50000


# ------------------------------------------------------------
# 4. Retrouver le SUMMARY DU RUN 6H
# ------------------------------------------------------------

LONG_SUMMARY=$(
  find data/market_physics_v3/state_tape_stream \
    -name SUMMARY.json \
    -type f \
  | sort \
  | tail -1
)

echo
echo "===== SUMMARY ====="
echo "$LONG_SUMMARY"

cat "$LONG_SUMMARY"


# ------------------------------------------------------------
# 5. Vérifier les fichiers construits
# ------------------------------------------------------------

LONG_TAPE=$(dirname "$LONG_SUMMARY")

echo
echo "===== PARQUET PARTS ====="

find "$LONG_TAPE" \
  -name 'part-*.parquet' \
  -type f \
  | sort

echo
echo "===== SUCCESS MARKER ====="

cat "$LONG_TAPE/_SUCCESS"


echo
echo "============================================================"
echo "POST-PROCESSING 6H TERMINE"
echo
echo "NE PAS RELANCER LA COLLECTE 6H."
echo
echo "Envoie-moi seulement :"
echo "1. le resultat des 5 tests"
echo "2. les lignes [state-tape]"
echo "3. le SUMMARY.json final"
echo "============================================================"
