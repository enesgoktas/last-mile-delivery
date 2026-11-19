from math import atan2, cos, radians, sin, sqrt


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth in metres."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _point_in_polygon(lon: float, lat: float, polygon: list[list[float]]) -> bool:
    inside = False
    n = len(polygon)
    if n < 3:
        return False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def point_in_geojson_polygon(lat: float, lon: float, geojson: dict) -> bool:
    """Validate whether a point is inside a GeoJSON Polygon or MultiPolygon.

    Supported formats:
    - {\"type\": \"Polygon\", \"coordinates\": [[[lon, lat], ...]]}
    - {\"type\": \"MultiPolygon\", \"coordinates\": [[[[lon, lat], ...]], ...]}
    """
    if not geojson:
        return False
    geometry_type = geojson.get("type")
    coordinates = geojson.get("coordinates") or []

    if geometry_type == "Polygon":
        if not coordinates:
            return False
        outer_ring = coordinates[0]
        return _point_in_polygon(lon, lat, outer_ring)

    if geometry_type == "MultiPolygon":
        return any(polygon and _point_in_polygon(lon, lat, polygon[0]) for polygon in coordinates)

    return False


_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode_geohash(lat: float, lon: float, precision: int = 6) -> str:
    """Encode latitude/longitude into a geohash string."""
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    even = True
    output: list[str] = []

    while len(output) < precision:
        if even:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                ch |= bits[bit]
                lon_range[0] = mid
            else:
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_range[0] = mid
            else:
                lat_range[1] = mid

        even = not even
        if bit < 4:
            bit += 1
        else:
            output.append(_GEOHASH_BASE32[ch])
            bit = 0
            ch = 0

    return "".join(output)
