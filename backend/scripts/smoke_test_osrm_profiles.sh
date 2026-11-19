#!/usr/bin/env bash
# Smoke test: verify that the truck OSRM graph routes around hgv=no roads.
#
# Prerequisites:
#   1. Run: bash scripts/prepare_osrm_data.sh
#   2. Run: docker compose up -d osrm-car osrm-truck
#   3. Wait for both containers to become healthy.
#
# Usage:
#   bash scripts/smoke_test_osrm_profiles.sh
#   bash scripts/smoke_test_osrm_profiles.sh http://localhost:5001 http://localhost:5002
#
# Exit codes:
#   0 — truck route is measurably longer than car route (profiles differ)
#   1 — routes are identical or truck route is shorter (profile not working)
#   2 — one or both OSRM servers unreachable

set -euo pipefail

CAR_URL="${1:-http://localhost:5001}"
TRUCK_URL="${2:-http://localhost:5002}"

# Cross-Bosphorus test pair:
#   European side: near Kazlıçeşme (Avrasya Tunnel north portal area)
#   Asian side:    near Kozyatağı
EUROPE_LON=28.9834
EUROPE_LAT=41.0061
ASIA_LON=29.0872
ASIA_LAT=40.9865

COORDS="${EUROPE_LON},${EUROPE_LAT};${ASIA_LON},${ASIA_LAT}"

echo "==> Testing car profile ($CAR_URL) ..."
CAR_RESPONSE=$(curl -sf --max-time 10 \
    "${CAR_URL}/route/v1/driving/${COORDS}?overview=false" 2>&1) || {
    echo "ERROR: car OSRM server unreachable at $CAR_URL"
    exit 2
}
CAR_DISTANCE=$(echo "$CAR_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['routes'][0]['distance'])")
CAR_DURATION=$(echo "$CAR_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['routes'][0]['duration'])")
echo "    Car  — distance: ${CAR_DISTANCE}m  duration: ${CAR_DURATION}s"

echo "==> Testing truck profile ($TRUCK_URL) ..."
TRUCK_RESPONSE=$(curl -sf --max-time 10 \
    "${TRUCK_URL}/route/v1/driving/${COORDS}?overview=false" 2>&1) || {
    echo "ERROR: truck OSRM server unreachable at $TRUCK_URL"
    exit 2
}
TRUCK_DISTANCE=$(echo "$TRUCK_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['routes'][0]['distance'])")
TRUCK_DURATION=$(echo "$TRUCK_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['routes'][0]['duration'])")
echo "    Truck — distance: ${TRUCK_DISTANCE}m  duration: ${TRUCK_DURATION}s"

# The truck must take a longer route than the car because:
#   - car can use the Avrasya Tunnel (hgv=no)
#   - truck must use the Bosphorus Bridge or Fatih Sultan Mehmet Bridge
# Expected delta: truck route is roughly 10+ km longer (bridges vs. tunnel).
DELTA=$(python3 -c "print(${TRUCK_DISTANCE} - ${CAR_DISTANCE})")
echo ""
echo "==> Distance delta (truck − car): ${DELTA}m"

THRESHOLD=5000   # 5 km minimum expected detour
PASS=$(python3 -c "print('yes' if ${TRUCK_DISTANCE} > ${CAR_DISTANCE} + ${THRESHOLD} else 'no')")

if [ "$PASS" = "yes" ]; then
    echo "==> PASS: truck routes around hgv=no roads (delta > ${THRESHOLD}m). Profile is working."
    exit 0
else
    echo "==> FAIL: truck route is not significantly longer than car route."
    echo "    Possible causes:"
    echo "      - osrm_data/truck/ was built with car.lua instead of truck.lua"
    echo "      - truck.lua does not correctly handle hgv=no access tags"
    echo "      - the test coordinates don't cross any hgv=no segment"
    exit 1
fi
