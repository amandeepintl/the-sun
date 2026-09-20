## What does this change?

<!-- One or two sentences: the what and the why. -->

## How was it tested?

<!-- Check what applies. CI runs the same commands. -->

- [ ] `python -m ruff check .` and `python -m ruff format --check .`
- [ ] `python -m mypy`
- [ ] `lint-imports` (architecture contracts)
- [ ] `python -m pytest -m unit -q`
- [ ] `python -m pytest -m integration -q` (needs real Postgres and Redis)

## Checklist

- [ ] No secrets, Discord IDs, model names or canned text hardcoded in `src/`
- [ ] Services still do not import `discord`; layering contracts hold
- [ ] New configuration documented in `.env.example` and the README tables
- [ ] Schema changes ship with an Alembic migration
