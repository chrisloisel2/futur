#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Binance Vision Downloader (FREE) + URL verification
# - Builds candidate URLs for the datasets you want
# - Verifies each URL (HTTP 200) BEFORE writing it to a manifest
# - Downloads only verified URLs
#
# Works with:
#   - Spot klines (1m OHLCV)  ✅ (best free source for 10y)
#   - Spot aggTrades         ✅ (tick trades)
#   - Futures UM klines      ✅ (if you want perp OHLCV)
#   - Futures premiumIndexKlines / markPriceKlines ✅ (often available)
#   - FundingRate / openInterest / ratios: depends on availability in Vision;
#     script probes and only keeps what exists.
#
# LIMITATION (important):
#   Historical full orderbook / BBO (bid/ask) is NOT reliably provided for 10y for free.
#   You can capture it going forward via websocket, but not reconstruct 10y for free.
# ============================================================

# ---------- USER CONFIG ----------
SYMBOL_SPOT="${SYMBOL_SPOT:-BTCUSDT}"
SYMBOL_FUT="${SYMBOL_FUT:-BTCUSDT}"     # USDⓈ-M perpetual symbol
INTERVAL="${INTERVAL:-1m}"

START_YEAR="${START_YEAR:-2016}"
END_YEAR="${END_YEAR:-2025}"

OUT_DIR="${OUT_DIR:-./binance_vision_downloads}"
MANIFEST="${MANIFEST:-${OUT_DIR}/manifest_verified_urls.txt}"

# Enable/disable families
DL_SPOT_KLINES="${DL_SPOT_KLINES:-1}"
DL_SPOT_AGGTRADES="${DL_SPOT_AGGTRADES:-1}"
DL_FUT_UM_KLINES="${DL_FUT_UM_KLINES:-1}"
DL_FUT_UM_MARKPRICE_KLINES="${DL_FUT_UM_MARKPRICE_KLINES:-1}"
DL_FUT_UM_PREMIUMINDEX_KLINES="${DL_FUT_UM_PREMIUMINDEX_KLINES:-1}"

# extra probes (often missing in Vision; we still probe)
DL_FUT_UM_FUNDINGRATE="${DL_FUT_UM_FUNDINGRATE:-1}"
DL_FUT_UM_OPENINTEREST="${DL_FUT_UM_OPENINTEREST:-1}"
DL_FUT_UM_LONGSHORT_RATIO="${DL_FUT_UM_LONGSHORT_RATIO:-1}"

# Concurrency for downloads
PARALLEL="${PARALLEL:-6}"

# ---------- END USER CONFIG ----------

BASE="https://data.binance.vision"

mkdir -p "${OUT_DIR}"
: > "${MANIFEST}"

log() { printf "[%s] %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >&2; }

http_ok() {
  # returns 0 if HTTP 200, else 1
  local url="$1"
  local code
  code="$(curl -sSIL --max-time 20 -o /dev/null -w "%{http_code}" "$url" || true)"
  [[ "$code" == "200" ]]
}

add_if_ok() {
  local url="$1"
  if http_ok "$url"; then
    echo "$url" >> "${MANIFEST}"
    return 0
  fi
  return 1
}

month_iter() {
  local y="$1"
  local m
  for m in $(seq -w 1 12); do
    echo "${y}-${m}"
  done
}

# ---------- URL builders (Vision standard layout) ----------
# Spot:
spot_klines_url() {
  local ym="$1" # YYYY-MM
  echo "${BASE}/data/spot/monthly/klines/${SYMBOL_SPOT}/${INTERVAL}/${SYMBOL_SPOT}-${INTERVAL}-${ym}.zip"
}
spot_aggtrades_url() {
  local ym="$1" # YYYY-MM
  echo "${BASE}/data/spot/monthly/aggTrades/${SYMBOL_SPOT}/${SYMBOL_SPOT}-aggTrades-${ym}.zip"
}

# Futures USDⓈ-M:
fut_um_klines_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/klines/${SYMBOL_FUT}/${INTERVAL}/${SYMBOL_FUT}-${INTERVAL}-${ym}.zip"
}
fut_um_markprice_klines_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/markPriceKlines/${SYMBOL_FUT}/${INTERVAL}/${SYMBOL_FUT}-${INTERVAL}-${ym}.zip"
}
fut_um_premiumindex_klines_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/premiumIndexKlines/${SYMBOL_FUT}/${INTERVAL}/${SYMBOL_FUT}-${INTERVAL}-${ym}.zip"
}

# The following families are not always present on Vision.
# We probe them anyway (script will keep only working URLs).
fut_um_fundingrate_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/fundingRate/${SYMBOL_FUT}/${SYMBOL_FUT}-fundingRate-${ym}.zip"
}
fut_um_openinterest_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/openInterestHist/${SYMBOL_FUT}/${SYMBOL_FUT}-openInterestHist-${ym}.zip"
}
fut_um_longshort_ratio_url() {
  local ym="$1"
  echo "${BASE}/data/futures/um/monthly/globalLongShortAccountRatio/${SYMBOL_FUT}/${SYMBOL_FUT}-globalLongShortAccountRatio-${ym}.zip"
}

# ---------- Build + verify ----------
log "Building & verifying URLs (HTTP 200 required) ..."
for y in $(seq "${START_YEAR}" "${END_YEAR}"); do
  while read -r ym; do
    # Spot klines (OHLCV 1m)
    if [[ "${DL_SPOT_KLINES}" == "1" ]]; then
      add_if_ok "$(spot_klines_url "$ym")" || true
    fi

    # Spot aggTrades (tick trades)
    if [[ "${DL_SPOT_AGGTRADES}" == "1" ]]; then
      add_if_ok "$(spot_aggtrades_url "$ym")" || true
    fi

    # Futures UM klines
    if [[ "${DL_FUT_UM_KLINES}" == "1" ]]; then
      add_if_ok "$(fut_um_klines_url "$ym")" || true
    fi

    # Futures markPriceKlines
    if [[ "${DL_FUT_UM_MARKPRICE_KLINES}" == "1" ]]; then
      add_if_ok "$(fut_um_markprice_klines_url "$ym")" || true
    fi

    # Futures premiumIndexKlines
    if [[ "${DL_FUT_UM_PREMIUMINDEX_KLINES}" == "1" ]]; then
      add_if_ok "$(fut_um_premiumindex_klines_url "$ym")" || true
    fi

    # Futures fundingRate (probe)
    if [[ "${DL_FUT_UM_FUNDINGRATE}" == "1" ]]; then
      add_if_ok "$(fut_um_fundingrate_url "$ym")" || true
    fi

    # Futures openInterestHist (probe)
    if [[ "${DL_FUT_UM_OPENINTEREST}" == "1" ]]; then
      add_if_ok "$(fut_um_openinterest_url "$ym")" || true
    fi

    # Futures globalLongShortAccountRatio (probe)
    if [[ "${DL_FUT_UM_LONGSHORT_RATIO}" == "1" ]]; then
      add_if_ok "$(fut_um_longshort_ratio_url "$ym")" || true
    fi

  done < <(month_iter "$y")
done

COUNT="$(wc -l < "${MANIFEST}" | tr -d ' ')"
log "Verified URLs: ${COUNT}"
log "Manifest: ${MANIFEST}"

if [[ "${COUNT}" -eq 0 ]]; then
  log "No working URLs found. Check SYMBOL/years/interval."
  exit 1
fi

# ---------- Download verified URLs ----------
log "Downloading verified URLs to: ${OUT_DIR}"
export OUT_DIR

download_one() {
  local url="$1"
  local rel="${url#${BASE}/}"
  local out="${OUT_DIR}/${rel}"
  mkdir -p "$(dirname "$out")"

  if [[ -s "$out" ]]; then
    return 0
  fi

  # resume + retries
  curl -fSL --retry 6 --retry-delay 2 --connect-timeout 10 --max-time 0 \
    -o "$out" "$url"
}

export -f download_one
export BASE

# parallel download
cat "${MANIFEST}" | xargs -n 1 -P "${PARALLEL}" -I {} bash -c 'download_one "$@"' _ {}

log "Done."
log "Next step: unzip + convert to parquet + build 1m aligned features + labels."
