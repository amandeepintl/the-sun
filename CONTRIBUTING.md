# Contributing to The Sun

Thank you for considering a contribution. This project values correctness over
convenience: real integrations, typed errors, and nothing hardcoded that should
be configuration.

## Development setup

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows (Git Bash); .venv/bin/activate on Unix
pip install -e ".[dev]"

cp .env.example .env                 # fill in real values; .env is gitignored
python -m the_sun check-config
```

## Before you open a pull request

Run the same checks CI runs:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
lint-imports                        # architecture contracts
python -m pytest -m unit -q         # no infrastructure needed
```

Integration tests need real PostgreSQL and Redis configured in `.env`:

```bash
python -m pytest -m integration -q
```

## Rules the code is held to

1. **Layering.** Commands call services; services call repositories, cache and
   providers. Services never import `discord`. The contracts in
   `pyproject.toml` are enforced by `lint-imports` and
   `tests/unit/test_architecture.py`.
2. **No bypass paths.** Only repositories contain SQL, only `cache/` imports
   `redis`, only `config/` reads the environment, only `cogs/` and `bot.py`
   import `discord`.
3. **Nothing dynamic is hardcoded.** No Discord IDs, credentials, model names,
   prices or canned AI text in `src/`. New behaviour is configured, not coded.
4. **Real integrations only.** No dry-run mode, no fake provider, database or
   cache in the running application. Test doubles live exclusively in `tests/`.
5. **Secrets stay in the environment.** Never commit `.env` or any credential.

## Commit and pull request style

- Imperative mood, one logical change per commit
  (for example: `Add per-guild quota to the rate limiter`).
- Describe the *why* in the pull request body, and note any schema migration.
- New commands or settings belong in the README tables and `.env.example` in
  the same change.

## Reporting issues

Open a bug report or feature request using the templates under
`.github/ISSUE_TEMPLATE`. For security vulnerabilities, do **not** open a
public issue - see `SECURITY.md`.
