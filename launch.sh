#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ACTION="start"
if [[ $# -gt 0 && "$1" =~ ^(start|stop|status|logs)$ ]]; then
  ACTION="$1"
  shift
fi

NO_API=0
NO_FRONT=0
SKIP_SETUP=0
NO_INSTALL=0

usage() {
  cat <<'EOF'
Usage:
  ./launch.sh [start] [--no-api] [--no-front] [--skip-setup] [--no-install]
  ./launch.sh stop
  ./launch.sh status
  ./launch.sh logs

Environment:
  API_HOST=0.0.0.0
  API_PORT=8000
  FRONTEND_HOST=0.0.0.0
  FRONTEND_PORT=3000
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-api)
      NO_API=1
      shift
      ;;
    --no-front)
      NO_FRONT=1
      shift
      ;;
    --skip-setup)
      SKIP_SETUP=1
      shift
      ;;
    --no-install)
      NO_INSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Docker Compose is required." >&2
  exit 1
fi

case "$ACTION" in
  stop)
    "${COMPOSE[@]}" down
    exit 0
    ;;
  status)
    "${COMPOSE[@]}" ps
    exit 0
    ;;
  logs)
    "${COMPOSE[@]}" logs -f
    exit 0
    ;;
esac

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8000}"
export FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
export FRONTEND_PORT="${FRONTEND_PORT:-3000}"
export FRONTEND_ORIGINS="${FRONTEND_ORIGINS:-http://localhost:${FRONTEND_PORT},http://127.0.0.1:${FRONTEND_PORT}}"
export FRONTEND_ORIGIN_REGEX="${FRONTEND_ORIGIN_REGEX:-https?://.*:${FRONTEND_PORT}}"

export FUTUR_MONGO_URI="${FUTUR_MONGO_URI:-mongodb://localhost:27017}"
export FUTUR_MONGO_DB="${FUTUR_MONGO_DB:-trader}"
export MONGODB_URI="${MONGODB_URI:-$FUTUR_MONGO_URI}"
export MONGODB_DB="${MONGODB_DB:-$FUTUR_MONGO_DB}"
export FUTUR_MONGO_SOURCE_COLLECTION="${FUTUR_MONGO_SOURCE_COLLECTION:-historical_ohlcv}"
export FUTUR_MONGO_FEATURE_COLLECTION="${FUTUR_MONGO_FEATURE_COLLECTION:-historical_ohlcv_enriched}"
export MONGODB_FEATURE_COLLECTION="${MONGODB_FEATURE_COLLECTION:-$FUTUR_MONGO_FEATURE_COLLECTION}"
export MONGODB_HIST_COLLECTION="${MONGODB_HIST_COLLECTION:-$FUTUR_MONGO_FEATURE_COLLECTION}"
export QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

services=(mongodb qdrant)
if [[ "$NO_FRONT" -eq 0 ]]; then
  services+=(frontend)
fi

echo "Starting Docker services: ${services[*]}"
"${COMPOSE[@]}" up -d "${services[@]}"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Python 3 is required to start the API." >&2
  exit 1
fi

if [[ "$SKIP_SETUP" -eq 0 ]]; then
  echo "Preparing MongoDB indexes and collections"
  "$PYTHON_BIN" "$ROOT_DIR/scripts/setup_mongodb.py"
fi

if [[ "$NO_API" -eq 0 && "$NO_INSTALL" -eq 0 ]]; then
  if ! "$PYTHON_BIN" - <<'PY'
import importlib.util
required = ["fastapi", "uvicorn", "pyarrow", "yaml", "sklearn", "pymongo", "httpx", "dotenv"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("missing: " + ", ".join(missing))
PY
  then
    echo "Installing API dependencies from requirements-api.txt"
    "$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements-api.txt"
  fi
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Frontend: http://0.0.0.0:${FRONTEND_PORT}"
if [[ -n "$LAN_IP" ]]; then
  echo "Frontend LAN: http://${LAN_IP}:${FRONTEND_PORT}"
fi
echo "API: http://${API_HOST}:${API_PORT}"
echo "API docs: http://${API_HOST}:${API_PORT}/docs"
echo

if [[ "$NO_API" -eq 1 ]]; then
  echo "API disabled by --no-api. Docker services are running in background."
  exit 0
fi

echo "frontend_pipeline/api_server.py was retired during the Phase 2 rebuild" >&2
echo "(mixed API/autonomous-loop/EMA-fallback/Mongo/non-canonical paper account" >&2
echo "-- see docs/FOUNDATION_AUDIT.md). No replacement API exists yet; none will" >&2
echo "until the Truth Engine is stable. Use --no-api to run Docker services only." >&2
exit 1
