#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${OUT_DIR:-./data_bitstamp}"
mkdir -p "$OUT_DIR"

HIST_URL="https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz"
UPD_URL="https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data/updates/btcusd_bitstamp_1min_latest.csv"

HIST_OUT="$OUT_DIR/btcusd_bitstamp_1min_2012-2025.csv.gz"
UPD_OUT="$OUT_DIR/btcusd_bitstamp_1min_latest.csv"

check_url() {
  local url="$1"
  local code
  code="$(curl -sS -L -o /dev/null -w "%{http_code}" -I "$url" || true)"
  if [[ "$code" != "200" ]]; then
    echo "[FAIL] HTTP $code :: $url" >&2
    return 1
  fi
  echo "[OK]   HTTP $code :: $url"
}

download() {
  local url="$1"
  local out="$2"
  echo "[DL] $out"
  curl -sS -L --fail --retry 5 --retry-delay 2 --connect-timeout 10 -o "$out" "$url"
}

echo "== Checking URLs =="
check_url "$HIST_URL"
check_url "$UPD_URL"

echo "== Downloading =="
download "$HIST_URL" "$HIST_OUT"
download "$UPD_URL" "$UPD_OUT"

echo "== Done =="
ls -lh "$OUT_DIR"
