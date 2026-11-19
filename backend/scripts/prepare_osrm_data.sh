#!/usr/bin/env bash
set -euo pipefail

# Downloads Istanbul OSM extract from BBBike and runs OSRM pre-processing
# (extract → partition → customize) using the MLD algorithm for TWO profiles:
#
#   • car   — default OSRM car.lua  (cars, vans, motorcycles)
#   • truck — custom truck.lua      (HGVs; respects hgv=no, maxweight, maxheight, etc.)
#
# Source: BBBike.org city extract (bbox 28.66–29.27 E, 40.90–41.27 N)
#         Covers both European and Asian sides + all 3 Bosphorus bridges.
#
# Output:
#   ./osrm_data/car/Istanbul.osrm.*   — used by osrm-car container (port 5001)
#   ./osrm_data/truck/Istanbul.osrm.* — used by osrm-truck container (port 5002)
#
# Requirements: docker, curl (pre-installed on macOS and most Linux distros)
# Disk: ~42 MB download, ~200–400 MB processed (two graphs)
# RAM:  ~1 GB per extract run
# Time: 3–6 min depending on machine
#
# NOTE: osrm-extract (v5.27.1) does not support an -o output flag. It always
# writes the graph files next to the input PBF. We therefore copy the PBF
# into each profile directory, run extract in place, then remove the copy.

REGION_URL="https://download.bbbike.org/osm/bbbike/Istanbul/Istanbul.osm.pbf"
BASE_DATA_DIR="./osrm_data"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:v5.27.1"
PBF_FILE="Istanbul.osm.pbf"
PBF_PATH="${BASE_DATA_DIR}/${PBF_FILE}"

# Profile directories
CAR_DIR="${BASE_DATA_DIR}/car"
TRUCK_DIR="${BASE_DATA_DIR}/truck"
PROFILES_DIR="$(cd "$(dirname "$0")/.." && pwd)/osrm_profiles"

echo "==> Creating data directories..."
mkdir -p "${CAR_DIR}" "${TRUCK_DIR}" "${BASE_DATA_DIR}"

echo "==> Downloading Istanbul OSM extract from BBBike (~42 MB, skips if already up to date)..."
if [ -f "${PBF_PATH}" ]; then
    curl -L --progress-bar -z "${PBF_PATH}" -o "${PBF_PATH}" "${REGION_URL}"
else
    curl -L --progress-bar -o "${PBF_PATH}" "${REGION_URL}"
fi

# ---------------------------------------------------------------------------
# CAR profile (uses OSRM's bundled /opt/car.lua)
# osrm-extract writes Istanbul.osrm.* next to the input PBF, so we copy the
# PBF into the car directory first, then clean it up afterwards.
# ---------------------------------------------------------------------------
echo ""
echo "==> [CAR] Copying PBF into car directory..."
cp "${PBF_PATH}" "${CAR_DIR}/${PBF_FILE}"

echo "==> [CAR] OSRM extract (building edge-expanded graph with car.lua)..."
docker run --rm \
    -v "$(cd "${CAR_DIR}" && pwd):/data" \
    "${OSRM_IMAGE}" \
    osrm-extract -p /opt/car.lua "/data/${PBF_FILE}"

echo "==> [CAR] OSRM partition (MLD cell decomposition)..."
docker run --rm \
    -v "$(cd "${CAR_DIR}" && pwd):/data" \
    "${OSRM_IMAGE}" \
    osrm-partition /data/Istanbul.osrm

echo "==> [CAR] OSRM customize (MLD metric customization)..."
docker run --rm \
    -v "$(cd "${CAR_DIR}" && pwd):/data" \
    "${OSRM_IMAGE}" \
    osrm-customize /data/Istanbul.osrm

echo "==> [CAR] Removing PBF copy from car directory..."
rm -f "${CAR_DIR}/${PBF_FILE}"
echo "==> [CAR] Done."

# ---------------------------------------------------------------------------
# TRUCK profile (uses our custom osrm_profiles/truck.lua)
# Same copy-in-place pattern as above.
# ---------------------------------------------------------------------------
echo ""
echo "==> [TRUCK] Copying PBF into truck directory..."
cp "${PBF_PATH}" "${TRUCK_DIR}/${PBF_FILE}"

echo "==> [TRUCK] OSRM extract (building edge-expanded graph with truck.lua)..."
echo "    Profile: ${PROFILES_DIR}/truck.lua"
docker run --rm \
    -v "$(cd "${TRUCK_DIR}" && pwd):/data" \
    -v "${PROFILES_DIR}:/profiles" \
    "${OSRM_IMAGE}" \
    osrm-extract -p /profiles/truck.lua "/data/${PBF_FILE}"

echo "==> [TRUCK] OSRM partition (MLD cell decomposition)..."
docker run --rm \
    -v "$(cd "${TRUCK_DIR}" && pwd):/data" \
    "${OSRM_IMAGE}" \
    osrm-partition /data/Istanbul.osrm

echo "==> [TRUCK] OSRM customize (MLD metric customization)..."
docker run --rm \
    -v "$(cd "${TRUCK_DIR}" && pwd):/data" \
    "${OSRM_IMAGE}" \
    osrm-customize /data/Istanbul.osrm

echo "==> [TRUCK] Removing PBF copy from truck directory..."
rm -f "${TRUCK_DIR}/${PBF_FILE}"
echo "==> [TRUCK] Done."

# ---------------------------------------------------------------------------
# Version stamp (used by app to bust distance-matrix cache after re-prep)
# ---------------------------------------------------------------------------
VERSION_STAMP="osrm-$(date -u +%Y%m%d%H%M%S)"
echo "${VERSION_STAMP}" > "${BASE_DATA_DIR}/.version"
echo ""
echo "==> Wrote cache version stamp: ${VERSION_STAMP}"
echo ""
echo "==> All done. OSRM data ready:"
echo "    Car graph:   ${CAR_DIR}/"
echo "    Truck graph: ${TRUCK_DIR}/"
echo ""
echo "    Start both servers with:"
echo "    docker compose up -d osrm-car osrm-truck"
