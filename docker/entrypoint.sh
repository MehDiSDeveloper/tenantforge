#!/usr/bin/env sh
# Migrate, optionally seed, then exec whatever the image was told to run.
# Migrations run here rather than in a separate job so `docker compose up` is
# genuinely one command; a real deployment would move this to a pre-deploy step.
set -eu

echo "waiting for postgres..."
python - <<'PY'
import asyncio, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings

async def main() -> None:
    dsn = get_settings().async_dsn(admin=True)
    for attempt in range(60):
        engine = create_async_engine(dsn)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            return
        except Exception:
            await engine.dispose()
            await asyncio.sleep(1)
    sys.exit("postgres did not become reachable")

asyncio.run(main())
PY

echo "running migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-false}" = "true" ]; then
    echo "seeding demo data..."
    python -m scripts.seed
fi

exec "$@"
