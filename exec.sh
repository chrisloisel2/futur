cd /home/qbee/futur-data-v2

# ============================================================
# PHASE 5 — INFORMATION DISCOVERY DEV_PILOT
# ============================================================

# ------------------------------------------------------------
# 1. Pull
# ------------------------------------------------------------

git pull --ff-only origin research/market-physics-data-v3

git rev-parse HEAD

# Attendu :
# 437a5f8b0d549787498b55bae0b6468ba6d2c667


# ------------------------------------------------------------
# 2. Tests complets
# Attendu actuellement : 74 passed
# ------------------------------------------------------------

/home/qbee/futur/.venv/bin/python3 -m pytest \
  tests/unit/test_market_physics_v3.py \
  tests/unit/test_market_physics_collectors_v3.py \
  tests/unit/test_market_physics_qualification_window_v3.py \
  tests/unit/test_market_physics_hyperliquid_v3.py \
  tests/unit/test_market_physics_freshness_v3.py \
  tests/unit/test_market_physics_phase3_v3.py \
  tests/unit/test_market_physics_binance_bootstrap_v3.py \
  tests/unit/test_market_physics_phase4_v3.py \
  tests/unit/test_market_physics_streaming_v3.py \
  tests/unit/test_market_physics_phase5_v3.py \
  -v


# ============================================================
# 3. TEST DU BUILDER STREAMING
# Sur les 5 minutes DEJA collectées.
# Cela vérifie le nouveau chemin long-run sans nouvelle collecte.
# ============================================================

/home/qbee/futur/.venv/bin/python3 \
  scripts/build_market_physics_state_tape_stream_v3.py \
  --venues binance,bybit,okx,hyperliquid \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --cadence-ms 100 \
  --chunk-rows 5000


# Afficher le summary streaming
STREAM_SUMMARY=$(
  find data/market_physics_v3/state_tape_stream \
    -name SUMMARY.json \
    -type f \
  | sort \
  | tail -1
)

echo "===== STREAMING SMOKE SUMMARY ====="
cat "$STREAM_SUMMARY"


# ============================================================
# 4. SMOKE DE L'AUDIT PHASE 5
#
# IMPORTANT :
# --allow-short-smoke force SHORT_SMOKE_ONLY.
# Aucun résultat de ces 5 minutes ne peut devenir candidat alpha.
# ============================================================

TAPE_DIR=$(dirname "$STREAM_SUMMARY")

/home/qbee/futur/.venv/bin/python3 \
  scripts/audit_market_physics_information_v3.py \
  --tape "$TAPE_DIR" \
  --cadence-ms 100 \
  --allow-short-smoke \
  --out reports/market_physics_v3/phase5_smoke

cat reports/market_physics_v3/phase5_smoke/SUMMARY.json


# ============================================================
# 5. Vérifications avant la collecte longue
# ============================================================

echo "===== NTP ====="
timedatectl show -p NTPSynchronized --value

echo "===== DISK ====="
df -h /home/qbee

echo "===== CURRENT STORAGE ====="
du -sh data/market_physics_v3


# ============================================================
# 6. DEV_PILOT — 6 HEURES SIMULTANEES
#
# C'est la première vraie fenêtre utilisée pour rechercher
# de l'information prédictive.
# ============================================================

echo "===== START 6H DEV_PILOT ====="

/home/qbee/futur/.venv/bin/python3 \
  scripts/collect_market_physics_v3.py \
  --venues binance,bybit,okx,hyperliquid \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --duration-seconds 21600


# ============================================================
# 7. Vérifier les health après 6h
# ============================================================

echo "===== BINANCE ====="
cat reports/market_physics_v3/health/binance.json

echo "===== BYBIT ====="
cat reports/market_physics_v3/health/bybit.json

echo "===== OKX ====="
cat reports/market_physics_v3/health/okx.json

echo "===== HYPERLIQUID ====="
cat reports/market_physics_v3/health/hyperliquid.json


# ============================================================
# 8. Construire le tape 100 ms EN STREAMING
#
# Pas de chargement des dizaines de millions d'événements
# en RAM.
# ============================================================

echo "===== BUILD 6H STREAMING STATE TAPE ====="

/home/qbee/futur/.venv/bin/python3 \
  scripts/build_market_physics_state_tape_stream_v3.py \
  --venues binance,bybit,okx,hyperliquid \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --cadence-ms 100 \
  --chunk-rows 50000


# ============================================================
# 9. Récupérer le nouveau tape 6h
# ============================================================

LONG_SUMMARY=$(
  find data/market_physics_v3/state_tape_stream \
    -name SUMMARY.json \
    -type f \
  | sort \
  | tail -1
)

LONG_TAPE=$(dirname "$LONG_SUMMARY")

echo "===== 6H STATE TAPE ====="
echo "$LONG_TAPE"
cat "$LONG_SUMMARY"


# ============================================================
# 10. PREMIERE AUTOPSIE INFORMATIONNELLE
#
# Horizons pré-enregistrés :
# 100ms / 500ms / 1s / 2s / 5s / 10s / 30s
# ============================================================

echo "===== PHASE 5 INFORMATION AUDIT ====="

/home/qbee/futur/.venv/bin/python3 \
  scripts/audit_market_physics_information_v3.py \
  --tape "$LONG_TAPE" \
  --cadence-ms 100 \
  --horizons-ms 100,500,1000,2000,5000,10000,30000 \
  --min-duration-hours 6 \
  --block-shuffle-repeats 100 \
  --max-block-shortlist 40 \
  --out reports/market_physics_v3/phase5_dev_pilot


# ============================================================
# 11. Résultats
# ============================================================

echo "===== PHASE 5 SUMMARY ====="

cat reports/market_physics_v3/phase5_dev_pilot/SUMMARY.json

echo
echo "===== CANDIDATE MECHANISMS ====="

/home/qbee/futur/.venv/bin/python3 - <<'PY'
import pandas as pd

p = "reports/market_physics_v3/phase5_dev_pilot/mechanisms.csv"
df = pd.read_csv(p)

print()
print("GENERAL_CANDIDATE")
print(
    df[df["classification"] == "GENERAL_CANDIDATE"]
    .sort_values("median_ic", key=lambda s: s.abs(), ascending=False)
    .to_string(index=False)
)

print()
print("SINGLE_SYMBOL_WATCH")
print(
    df[df["classification"] == "SINGLE_SYMBOL_WATCH"]
    .sort_values("median_ic", key=lambda s: s.abs(), ascending=False)
    .head(30)
    .to_string(index=False)
)
PY


echo
echo "============================================================"
echo "PHASE 5 DEV_PILOT TERMINE"
echo
echo "A me transmettre :"
echo "1. résultat pytest"
echo "2. SUMMARY du streaming 6h"
echo "3. SUMMARY Phase 5"
echo "4. GENERAL_CANDIDATE"
echo "5. SINGLE_SYMBOL_WATCH"
echo "============================================================"
