"""Model registry.

Importing this module registers every table with ``Base.metadata``; the
Alembic environment imports it for exactly that reason.
"""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.domain import Customer, Order, OrderItem, OrderStatus
from app.models.idempotency import IdempotencyKey
from app.models.rbac import Role, RolePermission, UserRole
from app.models.tenant import Tenant
from app.models.token import RefreshToken
from app.models.user import User

# Tables carrying a tenant_id and therefore an RLS policy. The migration and
# tests/test_tenant_isolation.py both read this list, so a new tenant-scoped
# table cannot ship without a policy.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "users",
    "roles",
    "role_permissions",
    "user_roles",
    "refresh_tokens",
    "audit_logs",
    "customers",
    "orders",
    "order_items",
    "idempotency_keys",
)

__all__ = [
    "TENANT_SCOPED_TABLES",
    "AuditLog",
    "Base",
    "Customer",
    "IdempotencyKey",
    "Order",
    "OrderItem",
    "OrderStatus",
    "RefreshToken",
    "Role",
    "RolePermission",
    "Tenant",
    "User",
    "UserRole",
]
