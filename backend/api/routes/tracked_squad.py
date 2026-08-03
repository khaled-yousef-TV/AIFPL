"""
Tracked-squad API routes.

The persistent Hermes-driven squad — GETting current state, seeding from
the latest Best Squad run, and (dev) resetting. Auto-apply and scoring are
triggered by the scheduler, not by API calls.
"""

import logging

from fastapi import APIRouter, HTTPException

from services import tracked_squad_service as svc

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_tracked_squad():
    """
    Full tracked-squad snapshot: current state + history + per-GW ledger.
    Returns {"seeded": false} when no state exists yet (empty state for UI).
    """
    try:
        data = svc.current_state()
        if data is None:
            return {"seeded": False}
        return {"seeded": True, **data}
    except Exception as e:
        logger.error(f"Error fetching tracked squad: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seed")
async def seed_tracked_squad():
    """
    Seed the tracked squad from the latest completed Best Squad Hermes run.
    Idempotent: 409 if a state already exists (reset first to re-seed).
    """
    try:
        state = svc.seed_from_best_squad()
        return {"seeded": True, "state": state}
    except ValueError as e:
        # Already-seeded / no source run — a client-side condition, not 500.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error seeding tracked squad: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_tracked_squad():
    """Dev/reseed: wipe both tracked-squad tables. Returns rows deleted."""
    try:
        deleted = svc.reset()
        return {"deleted": deleted}
    except Exception as e:
        logger.error(f"Error resetting tracked squad: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
