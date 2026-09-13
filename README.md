# TenantForge

A production-shaped **multi-tenant SaaS backend starter** in Python: FastAPI,
async SQLAlchemy 2.0, PostgreSQL. Tenant provisioning, JWT access tokens with
rotating refresh tokens, per-tenant RBAC, an audit trail, and one small real
domain (customers and orders) to show the pattern end to end.

The point of the repository is a single claim, and the test suite exists to
try to break it:

> **A request can only ever see the workspace its token names — and that is
> enforced by PostgreSQL, not by application code remembering to filter.**

---

## 30-second quickstart

```bash
git clone <this repo> && cd tenantforge
docker compose up --build
```

That starts PostgreSQL, runs the migrations, seeds two demo workspaces and
serves the API. Then:

* **http://localhost:8000/docs** — interactive OpenAPI
* **http://localhost:8000/health** — liveness + database reachability

Sign in as either workspace and watch the same endpoints return different data:

```bash
curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"tenant_slug":"northwind","email":"owner@northwind.example","password":"correct-horse-battery-staple"}'
```

Take the `access_token` from the response and:

```bash
curl -s localhost:8000/api/v1/customers -H "authorization: Bearer $TOKEN"
```

Now log in as `owner@acme.example` (tenant `acme`, same password), and try to
`GET /api/v1/customers/<an id you saw in northwind>`. You get **404** — not
403, and not somebody else's data.

### Running it without Docker

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                       # then edit SECRET_KEY
docker compose -f docker-compose.test.yml up -d   # or bring your own Postgres
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

### Tests

```bash
docker compose -f docker-compose.test.yml up -d
pytest -q
```

The suite needs a real PostgreSQL. It is testing row-level security policies,
which SQLite cannot express — a suite that swapped in SQLite would go green
while proving nothing about the one property the design rests on.

---

## The isolation strategy, and why the alternatives were rejected

**Chosen: one database, one schema, a `tenant_id` column on every tenant-owned
table, and PostgreSQL Row-Level Security enforcing it.**

Three things make it real rather than decorative:

1. **The application connects as a role that cannot escape a policy.** The
   first migration provisions `tenantforge_app` with `NOSUPERUSER` and
   `NOBYPASSRLS`. A superuser — or any role with `BYPASSRLS` — ignores every
   policy in the database, so "we have RLS" is worth nothing until you can say
   which role the app connects as. `tests/test_tenant_isolation.py` asserts
   the attributes rather than assuming them.
2. **Every policy is `FORCE`d.** Without `ALTER TABLE ... FORCE ROW LEVEL
   SECURITY`, a table's *owner* is exempt from its own policies — which is
   exactly the role migrations and seeds run as, and exactly the case a
   locally-run-as-superuser test fails to notice.
3. **The tenant is a transaction-local setting, not a query parameter.** The
   request dependency runs `set_config('app.current_tenant', <id>, true)` on
   the session, and every policy reads it through a `current_tenant_id()`
   function. `is_local => true` means the value dies with the transaction and
   cannot leak onto the next checkout of a pooled connection.

Each policy has both halves:

```sql
CREATE POLICY tenant_isolation ON customers
    USING      (tenant_id = current_tenant_id())   -- what may be read
    WITH CHECK (tenant_id = current_tenant_id());  -- what may be written
```

`USING` is why an id from another workspace resolves to nothing. `WITH CHECK`
is why a service that computed the wrong `tenant_id` — from a request body,
say — gets its `INSERT` refused instead of silently storing a leaked row.

**It fails closed.** With `app.current_tenant` unset, the predicate is `NULL`,
which is not `true`, so an unbound session reads *zero* rows rather than all of
them. The default state of a session that forgot to identify itself is
blindness.

### Why not a database per tenant

Strongest isolation, and the honest choice for a handful of large enterprise
customers with contractual data-residency terms. Rejected because the cost is
paid on every axis that matters for a starter: N connection pools or a
connection-routing layer, N migration runs per deploy (and a partial-failure
story for when run 43 of 200 fails), no cross-tenant query for billing or
support without a separate warehouse, and per-tenant provisioning that takes
seconds instead of milliseconds. It is the right *upgrade* for a specific
customer, and this design leaves room for it: the tenant resolution already
goes through one function.

### Why not a schema per tenant

The usual middle ground, and it fails in the middle. Migrations still run once
per tenant, `search_path` becomes load-bearing global state that is exactly as
easy to forget as a `WHERE` clause, and PostgreSQL's catalogue gets slow and
awkward somewhere in the low thousands of schemas. It buys real isolation only
if the app also connects as a per-tenant role — at which point the operational
cost is close to database-per-tenant without the benefit.

### Why not "just always filter by tenant_id"

This is the common answer and it is one forgotten `WHERE` clause away from a
breach — in a new endpoint, in a `JOIN` somebody wrote at speed, in a
`COUNT(*)` for a dashboard. It relies on every developer, forever, and it fails
*open*: the bug's symptom is more data, not less, so nothing crashes and no
test fails unless somebody thought to write that exact test. Under RLS the same
mistake returns an empty list, which is visible immediately.

The real cost of the chosen approach is honest and worth stating: policies are
invisible in the Python code, so a newcomer reading a repository method sees a
query with no tenant filter and has to *know* the database is adding one. That
is why the tenant story is at the top of this README, why `app/core/db.py`
documents it, and why the suite pins it in `pg_class` and `pg_policies`.

---

## Architecture

```
                 HTTP
                   │
   ┌───────────────▼────────────────┐
   │ app/api/          routers      │  thin: parse, delegate, choose a status
   │   deps.py         wiring       │
   └───────────────┬────────────────┘
                   │  Principal + AsyncSession
   ┌───────────────▼────────────────┐
   │ app/services/     business     │  no HTTP here; testable without a client
   └───────────────┬────────────────┘
                   │
   ┌───────────────▼────────────────┐
   │ app/repositories/ data access  │  no tenant filtering — see below
   └───────────────┬────────────────┘
                   │
   ┌───────────────▼────────────────┐
   │ PostgreSQL                     │
   │   ROW LEVEL SECURITY (FORCED)  │  ← the tenant boundary lives here
   └────────────────────────────────┘
```

The request path, in full:

```
Authorization: Bearer <jwt>
   └─ get_session()      an application-role session; no tenant bound yet,
                         so it can currently see nothing
      └─ get_principal() decode the token → set_config('app.current_tenant')
                         → load the user → build a Principal
         └─ require(Permission.ORDER_WRITE)
            └─ router → service(session, principal) → repository → SQL
```

Two engines exist, and the difference between them *is* the security model:

| Engine | Role | Used by |
|---|---|---|
| `_app_engine` | `tenantforge_app` (no `BYPASSRLS`) | every request |
| `_admin_engine` | owner | migrations, seeding, and the three operations that predate a tenant context |

Those three are: **provisioning a tenant** (the row defining the boundary does
not exist yet), **resolving a slug at login** (you cannot bind to a tenant
before you know which one), and **seeding**. They all live behind
`system_session()` in `app/core/db.py`, and only `app/services/provisioning.py`
calls it — so "which code can see across tenants?" has a one-file answer that
code review can hold.

### Layout

```
app/
  core/          config, engines, security, permissions, errors, logging,
                 rate limiting, pagination
  models/        SQLAlchemy tables (TENANT_SCOPED_TABLES is read by the
                 migration *and* the tests)
  schemas/       Pydantic request/response models
  repositories/  data access; queries, no policy
  services/      business logic; raises domain errors, never HTTPException
  api/           routers + dependency wiring
alembic/         one migration: schema, app role, RLS policies
tests/           test_tenant_isolation.py is the headline
```

### Security decisions worth naming

* **Argon2id** (`argon2-cffi` directly, OWASP profile: 19 MiB, t=2, p=1), with
  automatic rehash on login when the parameters change.
* **Refresh tokens are not JWTs.** They are opaque random strings; the database
  stores an HMAC-SHA256 digest keyed on `SECRET_KEY`. A leaked table yields no
  usable credential, and revocation is a row update rather than a denylist of
  signatures.
* **Rotation with reuse detection.** Refreshing consumes the presented token
  and issues a successor in the same family. Presenting a consumed token means
  a copy escaped, so the *whole family* is revoked — the legitimate holder is
  signed out and notices, instead of quietly sharing a session with a thief.
* **Stateless tokens that are still revocable.** `users.token_version` is a
  claim in the JWT; bumping the column invalidates every access token already
  issued to that user. Logout-everywhere, a password change, a deactivation and
  a role change all bump it.
* **UUID primary keys everywhere.** Sequential ids tell an attacker what to
  guess next and make "row 41 exists" observable.
* **404, not 403, for another workspace's data.** A 403 answers the only
  question an enumerator is asking.
* **One login failure message** for unknown tenant, unknown user, wrong
  password and deactivated account — plus a dummy Argon2 verification on the
  unknown-user path, so the answer does not leak through response latency.
* **Rate limits on the endpoints an attacker can reach unauthenticated**:
  login, refresh, registration.
* **No secrets in code.** `SECRET_KEY` has no default; the settings object
  refuses to build without one.

---

## Permissions

Permissions are a closed vocabulary in code (`app/core/permissions.py`); roles
are rows owned by a tenant. A workspace can invent "Warehouse supervisor"
without a deployment, but it can never invent a capability the code does not
already know how to check.

Every new workspace is seeded with four system roles:

| Role | Holds |
|---|---|
| `owner` | everything |
| `admin` | everything except `tenant:update` — running a workspace and owning it are different jobs |
| `member` | read everything, write customers and orders |
| `viewer` | read only |

Endpoints declare what they need — `Depends(require(Permission.ORDER_WRITE))` —
and `GET /api/v1/auth/me` tells a client exactly which permissions it holds.

## Concurrent edits

Two admins open the same customer, both edit, both save. Without a check the
second save silently erases the first, and the audit trail records both as if
nothing happened. So `customers` and `orders` carry a `version`:

```bash
curl -si localhost:8000/api/v1/customers/$ID -H "authorization: Bearer $TOKEN"
# ETag: "3"
curl -s -X PATCH localhost:8000/api/v1/customers/$ID -H "authorization: Bearer $TOKEN" \
  -H 'If-Match: "3"' -H 'content-type: application/json' -d '{"name":"Ada"}'
# 200, ETag: "4" -- or 412 precondition_failed if somebody else got there first
```

The check runs at two distances. **Between a client's read and its write**,
`If-Match` on `PATCH`/`DELETE` is compared to the row's current version and a
mismatch is a 412 carrying `current_version`. **Inside a request**, between our
`SELECT` and our `UPDATE`, `version` is SQLAlchemy's `version_id_col`, so the
`UPDATE` itself says `WHERE version = <what we loaded>`; a write that raced it
matches zero rows and becomes a 409 rather than a lost update.

`If-Match` is optional. Clients that do not send it keep last-write-wins, so
turning this on broke nobody; clients that do send it get RFC 9110 semantics,
including a 412 for a tag this API could never have issued. The logic is in
`app/core/concurrency.py`; `tests/test_concurrency.py` plays the second editor.

## Safe retries

A client whose `POST /orders` times out cannot tell whether the order was
placed. Retrying risks a duplicate; not retrying risks a lost order. So both
create endpoints accept an `Idempotency-Key`:

```bash
curl -si -X POST localhost:8000/api/v1/orders -H "authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" -H 'content-type: application/json' -d @order.json
# 201 -- and the same 201, same body, plus `Idempotent-Replayed: true` on every retry
```

The key is claimed by a row in `idempotency_keys` written **in the same
transaction** as the order. If the write rolls back, so does the claim, and a
retry runs for real. A retry that arrives while the original is still running
hits the same unique index, and PostgreSQL makes it wait until the original
transaction ends, so concurrent retries still produce exactly one order.

A key is bound to its endpoint and payload: reusing it for a different request
is a 422 `idempotency_key_reused`, not a replay of the wrong answer. Keys belong
to one user in one workspace, live under the same RLS policy as every other
table, and expire after `IDEMPOTENCY_KEY_TTL_SECONDS` (24 hours). Without the
header, nothing changes. See `app/services/idempotency.py` and
`tests/test_idempotency.py`.

## The test suite

`pytest -q` runs six files. The headline is `tests/test_tenant_isolation.py`,
written entirely from the attacker's side: it holds a valid token for workspace
A and a correct identifier belonging to workspace B, and asserts it gets
nothing. It attacks at both levels —

**Through the API:** cross-tenant read, update and delete of customers, orders
and users; an order body naming another workspace's customer; list endpoints
and their `total`; the audit trail; a refresh token replayed against the wrong
workspace.

**Directly against the database, as the application role:** an unbound session
sees zero rows; a bound session sees only its own, even when the query names
the other tenant explicitly; an `INSERT` stamped with a foreign `tenant_id` is
refused by `WITH CHECK`; the role has neither `rolsuper` nor `rolbypassrls` and
cannot `DISABLE ROW LEVEL SECURITY`; and every table in `TENANT_SCOPED_TABLES`
is checked in `pg_class`/`pg_policies` for forced RLS and exactly one policy —
so a new tenant-scoped table cannot ship without a policy.

The rest cover authentication (rotation, reuse detection, revocation on
password change), RBAC (each role's boundary, immediate effect of a role
change, built-in roles being immutable), the domain's non-CRUD rules, and a
pure-Python file that needs neither HTTP nor a database — which is the only
thing that makes the layering claim above worth making.

CI runs ruff, `ruff format --check`, **mypy in strict mode**, the suite against
a real PostgreSQL, and a job that does nothing but `docker compose up` and curl
`/health`, so the one-command promise cannot rot.

## What I would do next

In the order I would actually do it:

1. **Redis-backed rate limiting.** The in-process limiter is honest for one
   instance and honest about its limit: N replicas mean N times the allowance.
   The seam is already there — one class implementing `RateLimiterBackend`.
2. **Invite flow instead of admin-set passwords.** `POST /users` currently
   takes a password, which means an administrator knows a colleague's
   credential. A signed single-use invite token is the right shape.
3. **Refresh tokens in `HttpOnly; Secure; SameSite=Strict` cookies** for
   browser clients, keeping the JSON body for service-to-service callers. The
   rotation machinery does not change.
4. **A partitioned or archived `audit_logs`.** It is append-only and grows
   without bound; monthly partitions plus a retention job is the obvious move,
   and there is no job runner in this repo yet.
5. **Per-tenant limits and metering** — seats, API quota — as a `tenant_limits`
   table checked in the service layer, which is where a 402 belongs.
6. **Observability**: OpenTelemetry traces with `tenant_id` and `request_id` as
   span attributes (both are already ambient `ContextVar`s), and RED metrics
   per route.
7. **A `SELECT`-only replica session** for reporting, bound to the same policy
   with a read-only role.
8. **Outbound webhooks** with per-tenant signing secrets — the first feature
   that would need a queue, and the point at which the "no job runner" gap has
   to be closed properly.

Deliberately *not* here: a UI, billing integration, and email delivery. Each is
a real product decision, and stubbing them would make the repository larger
without making it more true.

## Licence

MIT — see [LICENSE](LICENSE).
