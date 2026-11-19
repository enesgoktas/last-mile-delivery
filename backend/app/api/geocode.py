"""Geocoding proxy endpoint — forwards address search to Photon."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.algorithms import photon_client
from app.api.auth import get_current_user

router = APIRouter()


@router.get("/geocode")
async def geocode_search(
    q: str = Query(..., min_length=2, description="Address search query"),
    limit: int = Query(5, ge=1, le=10, description="Maximum number of results"),
    _user=Depends(get_current_user),
):
    """Search for address suggestions using the configured Photon geocoder.

    Returns up to *limit* results, each with a human-readable ``display_name``
    and the corresponding ``lat`` / ``lon`` coordinates.
    """
    return await photon_client.search_addresses(q, limit=limit)
