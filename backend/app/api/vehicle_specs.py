import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_write_db
from app.models.enums import FuelType, UserRole, VehicleType
from app.models.user import User
from app.schemas.vehicle_spec import (
    EPAImportRequest,
    EPAMenuEntry,
    EPASearchResult,
    VehicleSpecCreate,
    VehicleSpecResponse,
    VehicleSpecUpdate,
)
from app.services import epa_client, vehicle_spec_service
from app.utils.auth import require_role

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role(UserRole.ADMIN))]
DispatcherDep = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.DISPATCHER))]


@router.post("", response_model=VehicleSpecResponse, status_code=201)
async def create_vehicle_spec(
    data: VehicleSpecCreate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Create a vehicle spec entry manually (admin only)."""
    return await vehicle_spec_service.create_vehicle_spec(db, data, current_user)


@router.get("", response_model=list[VehicleSpecResponse])
async def list_vehicle_specs(
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    current_user: DispatcherDep,
    vehicle_type: VehicleType | None = Query(default=None),
    fuel_type: FuelType | None = Query(default=None),
    make: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
):
    """List vehicle specs visible to this tenant (own + global catalog)."""
    specs, total = await vehicle_spec_service.list_vehicle_specs(
        db,
        current_user,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        make=make,
        limit=limit,
        offset=skip,
    )
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return specs


@router.get("/search/epa", response_model=list[EPASearchResult])
async def search_epa(
    current_user: AdminDep,
    year: int = Query(..., ge=1984, le=2100, description="Model year"),
    make: str = Query(..., min_length=1, description="Vehicle make, e.g. Toyota"),
    model: str = Query(..., min_length=1, description="Vehicle model, e.g. Corolla"),
):
    """Search the EPA fueleconomy.gov database for matching vehicles."""
    try:
        return await epa_client.search_vehicles(year=year, make=make, model=model)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EPA API request failed: {exc}",
        ) from exc


@router.get("/search/epa/years", response_model=list[EPAMenuEntry])
async def epa_years(current_user: AdminDep):
    """Return all model years available in the EPA database."""
    try:
        return await epa_client.get_years()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EPA API request failed: {exc}",
        ) from exc


@router.get("/search/epa/makes", response_model=list[EPAMenuEntry])
async def epa_makes(
    current_user: AdminDep,
    year: int = Query(..., ge=1984, le=2100),
):
    """Return vehicle makes for a given model year from the EPA database."""
    try:
        return await epa_client.get_makes(year=year)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EPA API request failed: {exc}",
        ) from exc


@router.get("/search/epa/models", response_model=list[EPAMenuEntry])
async def epa_models(
    current_user: AdminDep,
    year: int = Query(..., ge=1984, le=2100),
    make: str = Query(..., min_length=1),
):
    """Return vehicle models for a given year and make from the EPA database."""
    try:
        return await epa_client.get_models(year=year, make=make)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EPA API request failed: {exc}",
        ) from exc


@router.post("/import/epa", response_model=VehicleSpecResponse, status_code=201)
async def import_from_epa(
    data: EPAImportRequest,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Fetch a vehicle record from the EPA database by ID and import it into the catalog."""
    try:
        epa_result = await epa_client.get_vehicle_by_id(data.epa_vehicle_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"EPA API request failed: {exc}",
        ) from exc

    if epa_result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EPA vehicle {data.epa_vehicle_id!r} not found or could not be parsed",
        )

    return await vehicle_spec_service.import_epa_vehicle_spec(db, epa_result, current_user)


@router.get("/{spec_id}", response_model=VehicleSpecResponse)
async def get_vehicle_spec(
    spec_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: DispatcherDep,
):
    """Get a single vehicle spec by ID."""
    return await vehicle_spec_service.get_vehicle_spec(db, spec_id, current_user)


@router.put("/{spec_id}", response_model=VehicleSpecResponse)
async def update_vehicle_spec(
    spec_id: uuid.UUID,
    data: VehicleSpecUpdate,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Update a vehicle spec (admin only)."""
    return await vehicle_spec_service.update_vehicle_spec(db, spec_id, data, current_user)


@router.delete("/{spec_id}", status_code=204)
async def delete_vehicle_spec(
    spec_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_write_db)],
    current_user: AdminDep,
):
    """Delete a vehicle spec. Couriers linked to it will have vehicle_spec_id set to NULL."""
    await vehicle_spec_service.delete_vehicle_spec(db, spec_id, current_user)
