from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import audit, auth, customers, orders, roles, tenants, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(customers.router)
api_router.include_router(orders.router)
api_router.include_router(audit.router)
