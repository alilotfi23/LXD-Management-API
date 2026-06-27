"""Aggregator for all versioned (/api/v1) routers.

Each resource contributes its own `APIRouter`; this module includes them all
under a single `v1_router` that the main app mounts at `/api/v1`. Health stays
unversioned at `/health`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.instances import router as instances_router
from app.api.v1.routes.snapshots import router as snapshots_router
from app.api.v1.routes.backups import router as backups_router
from app.api.v1.routes.storage import router as storage_router
from app.api.v1.routes.networks import router as networks_router
from app.api.v1.routes.projects import router as projects_router
from app.api.v1.routes.images import router as images_router
from app.api.v1.routes.operations import router as operations_router
from app.api.v1.routes.system import router as system_router

v1_router = APIRouter()
v1_router.include_router(auth_router)
v1_router.include_router(instances_router)
v1_router.include_router(snapshots_router)
v1_router.include_router(backups_router)
v1_router.include_router(storage_router)
v1_router.include_router(networks_router)
v1_router.include_router(projects_router)
v1_router.include_router(images_router)
v1_router.include_router(operations_router)
v1_router.include_router(system_router)

# Later steps append: instances, snapshots, backups, storage, networks,
# projects, images, operations, system.
