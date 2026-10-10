"""Distance and travel-time helpers for the simulated service area."""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    mav_d_lat = radians(lat2 - lat1)
    mav_d_lon = radians(lon2 - lon1)
    mav_a = (
        sin(mav_d_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(mav_d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(mav_a))

def eta_minutes(distance_km: float, avg_speed_kmph: float) -> float:
    """Estimated travel time for a distance, at a fixed average speed."""
    if avg_speed_kmph <= 0:
        raise ValueError("avg_speed_kmph must be positive")
    return (distance_km / avg_speed_kmph) * 60.0
