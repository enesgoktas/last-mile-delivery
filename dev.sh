#!/usr/bin/env bash
# dev.sh — one-command local development startup
#
# Usage:
#   bash dev.sh               Start backend + frontend (default)
#   bash dev.sh --tracking    Start backend + tracking SPA only
#   bash dev.sh --all         Start backend + frontend + tracking (parallel)
#   bash dev.sh --stop        Stop all backend Docker services
#   bash dev.sh --logs        Tail backend logs
#   bash dev.sh --reset       Wipe database volumes and restart fresh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
TRACKING_DIR="$ROOT_DIR/tracking"

# ── Colours ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

step()  { echo -e "\n${BOLD}${BLUE}==>${NC} ${BOLD}$1${NC}"; }
ok()    { echo -e "    ${GREEN}✓${NC}  $1"; }
warn()  { echo -e "    ${YELLOW}⚠${NC}  $1"; }
err()   { echo -e "    ${RED}✗${NC}  $1"; }
info()  { echo -e "    ${CYAN}→${NC}  $1"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
FLAG="${1:-}"

if [[ "$FLAG" == "--stop" ]]; then
    echo -e "${BOLD}Stopping backend services...${NC}"
    cd "$BACKEND_DIR" && docker compose down
    echo -e "${GREEN}Done.${NC}"
    exit 0
fi

if [[ "$FLAG" == "--logs" ]]; then
    cd "$BACKEND_DIR" && docker compose logs -f --tail=50
    exit 0
fi

if [[ "$FLAG" == "--reset" ]]; then
    echo -e "${BOLD}${RED}This will wipe the database and all Docker volumes.${NC}"
    read -rp "    Are you sure? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
    cd "$BACKEND_DIR" && docker compose down -v
    ok "Volumes wiped — continuing with fresh start..."
fi

# Determine which web apps to start
START_FRONTEND=true
START_TRACKING=false
if [[ "$FLAG" == "--tracking" ]]; then
    START_FRONTEND=false
    START_TRACKING=true
elif [[ "$FLAG" == "--all" ]]; then
    START_FRONTEND=true
    START_TRACKING=true
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}  Last-Mile Delivery — Dev Startup${NC}"
echo -e "  ─────────────────────────────────"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites"

if ! command -v docker &>/dev/null; then
    err "Docker not found. Install it from https://docker.com"
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    err "Docker daemon is not running. Start Docker Desktop and try again."
    exit 1
fi
ok "Docker is running"

if ! command -v node &>/dev/null; then
    err "Node.js not found. Install it from https://nodejs.org"
    exit 1
fi
ok "Node.js $(node --version)"

if ! command -v npm &>/dev/null; then
    err "npm not found. It should come with Node.js."
    exit 1
fi
ok "npm $(npm --version)"

if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)
    IFS='.' read -r PYTHON_MAJOR PYTHON_MINOR <<< "$PYTHON_VERSION"
    ok "python3 ${PYTHON_VERSION}"
    if (( PYTHON_MAJOR < 3 || (PYTHON_MAJOR == 3 && PYTHON_MINOR < 11) )); then
        warn "python3 is below 3.11. Backend local tooling and CI target Python 3.11+."
        info "Use a 3.11 interpreter for local pytest/alembic/uvicorn flows outside Docker."
    fi
else
    warn "python3 not found. dev.sh can still run Docker services, but local backend tooling expects Python 3.11+."
fi

# ── 2. OSRM map data ──────────────────────────────────────────────────────────
step "Checking OSRM map data"

CAR_DATA_FILE="$BACKEND_DIR/osrm_data/car/Istanbul.osrm.names"
TRUCK_DATA_FILE="$BACKEND_DIR/osrm_data/truck/Istanbul.osrm.names"
OLD_FLAT_FILE="$BACKEND_DIR/osrm_data/Istanbul.osrm.names"

if [ ! -f "$CAR_DATA_FILE" ] || [ ! -f "$TRUCK_DATA_FILE" ]; then
    # Detect leftover flat-layout data from before the dual-profile switch
    if [ -f "$OLD_FLAT_FILE" ]; then
        warn "Found old single-profile OSRM data (osrm_data/Istanbul.osrm.*)."
        warn "The routing engine now uses two separate graphs (car + truck profiles)."
        warn "The old flat data cannot be used; the map data must be rebuilt."
        echo ""
    else
        warn "OSRM map data not found."
        echo ""
    fi
    echo -e "    This downloads Istanbul OSM road data (~42 MB) and pre-processes"
    echo -e "    it into two routing graphs (car + truck). Runs once; takes 3–6 min."
    echo ""
    read -rp "    Prepare map data now? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        step "Preparing OSRM map data (grab a coffee...)"
        cd "$BACKEND_DIR"
        bash scripts/prepare_osrm_data.sh
        cd "$ROOT_DIR"
        ok "Map data ready"
    else
        err "Cannot start without OSRM map data."
        info "Run manually: cd backend && bash scripts/prepare_osrm_data.sh"
        exit 1
    fi
else
    VERSION_FILE="$BACKEND_DIR/osrm_data/.version"
    VERSION=$( [ -f "$VERSION_FILE" ] && cat "$VERSION_FILE" || echo "unknown" )
    ok "Car routing graph found   (osrm_data/car/)"
    ok "Truck routing graph found (osrm_data/truck/)"
    ok "Map data version: $VERSION"
fi

# ── 3. Backend .env ───────────────────────────────────────────────────────────
step "Checking backend .env"

ENV_FILE="$BACKEND_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    info "Creating .env from .env.example..."
    cp "$BACKEND_DIR/.env.example" "$ENV_FILE"
    ok ".env created"
fi

# Auto-fill JWT_SECRET if empty
if grep -q "^JWT_SECRET=$" "$ENV_FILE"; then
    if command -v python3 &>/dev/null; then
        JWT_VAL=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        # macOS-compatible in-place sed
        sed -i.bak "s|^JWT_SECRET=$|JWT_SECRET=${JWT_VAL}|" "$ENV_FILE"
        rm -f "${ENV_FILE}.bak"
        ok "Auto-generated JWT_SECRET"
    else
        err "JWT_SECRET is empty and python3 not found to generate one."
        info "Edit backend/.env and set JWT_SECRET manually."
        exit 1
    fi
else
    ok ".env has JWT_SECRET set"
fi

# ── 4. Web app dependencies ───────────────────────────────────────────────────
step "Checking web app dependencies"

if $START_FRONTEND; then
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        info "Running npm install in frontend/..."
        cd "$FRONTEND_DIR" && npm install --silent && cd "$ROOT_DIR"
        ok "frontend dependencies installed"
    else
        ok "frontend/node_modules present"
    fi
fi

if $START_TRACKING; then
    if [ ! -d "$TRACKING_DIR/node_modules" ]; then
        info "Running npm install in tracking/..."
        cd "$TRACKING_DIR" && npm install --silent && cd "$ROOT_DIR"
        ok "tracking dependencies installed"
    else
        ok "tracking/node_modules present"
    fi
fi

# ── 5. Start backend Docker services ─────────────────────────────────────────
step "Starting backend services"
info "Building and starting: postgres, redis, osrm-car, osrm-truck, api, celery_worker, celery_beat"

cd "$BACKEND_DIR"
docker compose up -d --build 2>&1 | grep -E "^(#|Starting|Creating|Recreating| => |Error)" || true
cd "$ROOT_DIR"

# ── 6. Wait for API health ────────────────────────────────────────────────────
step "Waiting for API to be healthy"
info "This includes both OSRM servers loading their graphs (up to 90 s on first start)..."

MAX_WAIT=180
ELAPSED=0
API_URL="http://localhost:8000/health"

printf "    "
while true; do
    if curl -sf "$API_URL" &>/dev/null; then
        echo ""
        ok "API is healthy"
        break
    fi
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo ""
        err "API did not become healthy within ${MAX_WAIT}s."
        info "Check logs: bash dev.sh --logs"
        info "Check API specifically: cd backend && docker compose logs api"
        exit 1
    fi
    printf "."
    sleep 3
    ELAPSED=$((ELAPSED + 3))
done

# ── 7. Database migrations ────────────────────────────────────────────────────
step "Running database migrations"

cd "$BACKEND_DIR"
MIGRATION_OUTPUT=$(docker compose exec -T api alembic upgrade head 2>&1)
if echo "$MIGRATION_OUTPUT" | grep -q "ERROR"; then
    err "Migration failed:"
    echo "$MIGRATION_OUTPUT"
    exit 1
fi
cd "$ROOT_DIR"

if echo "$MIGRATION_OUTPUT" | grep -q "Running upgrade"; then
    ok "Migrations applied"
elif echo "$MIGRATION_OUTPUT" | grep -q "No migrations"; then
    ok "Already up to date"
else
    ok "Migrations complete"
fi

# ── 8. Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}  ║         Everything is running!           ║${NC}"
echo -e "${BOLD}${GREEN}  ╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Backend API${NC}      →  ${CYAN}http://localhost:8000${NC}"
echo -e "  ${BOLD}API Docs${NC}         →  ${CYAN}http://localhost:8000/docs${NC}"
if $START_FRONTEND; then
    echo -e "  ${BOLD}Frontend${NC}         →  ${CYAN}http://localhost:5173${NC}  (starting below)"
fi
if $START_TRACKING; then
    echo -e "  ${BOLD}Tracking SPA${NC}     →  ${CYAN}http://localhost:5175${NC}  (starting below)"
fi
echo ""
echo -e "  ${BOLD}Other commands:${NC}"
echo -e "    bash dev.sh --tracking  Backend + tracking SPA only"
echo -e "    bash dev.sh --all       Backend + frontend + tracking"
echo -e "    bash dev.sh --logs      Tail all backend logs"
echo -e "    bash dev.sh --stop      Stop backend services"
echo -e "    bash dev.sh --reset     Wipe DB and start fresh"
echo ""
echo -e "  ${YELLOW}Press Ctrl+C to stop the dev server(s).${NC}"
echo -e "  ${YELLOW}Backend services keep running in Docker — stop with: bash dev.sh --stop${NC}"
echo ""
echo -e "  ─────────────────────────────────────────────"

# ── 9. Dev server(s) ──────────────────────────────────────────────────────────
if $START_FRONTEND && $START_TRACKING; then
    # Run both in parallel; trap Ctrl+C to kill both
    echo -e "  Starting frontend and tracking in parallel..."
    echo ""

    (cd "$FRONTEND_DIR" && npm run dev) &
    FRONTEND_PID=$!

    (cd "$TRACKING_DIR" && npm run dev) &
    TRACKING_PID=$!

    trap 'kill $FRONTEND_PID $TRACKING_PID 2>/dev/null; exit 0' INT TERM
    wait $FRONTEND_PID $TRACKING_PID

elif $START_TRACKING; then
    echo -e "  Starting tracking SPA..."
    echo ""
    cd "$TRACKING_DIR"
    npm run dev

else
    echo -e "  Starting frontend..."
    echo ""
    cd "$FRONTEND_DIR"
    npm run dev
fi
