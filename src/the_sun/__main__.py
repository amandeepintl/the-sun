"""Command line entry point.

``check-config``
    Validates the environment without touching the network.
``doctor``
    Runs real checks against PostgreSQL, Redis, every configured AI provider and
    Discord, then reports what it actually found.
``migrate``
    Applies database migrations against the configured database.
``run``
    Starts the Discord bot.
``invite``
    Prints this application's install URL, looked up from its real application id.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from the_sun import __version__
from the_sun.config import Settings, require_valid_settings, validate_settings
from the_sun.errors import TheSunError
from the_sun.logging_setup import configure_logging
from the_sun.observability import get_metrics
from the_sun.services import HealthReport, HealthService, build_service_context

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_CHECK_FAILED = 2

_MISSING_ENV_HINT = (
    "required environment variables: DISCORD_TOKEN, DATABASE_URL, REDIS_URL, "
    "AI_PROVIDERS, DEFAULT_PROVIDER (see .env.example)"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="the-sun",
        description="The Sun - Discord AI assistant utilities",
    )
    parser.add_argument("--version", action="version", version=f"the-sun {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check-config", help="validate the environment without network access"
    )
    check.add_argument("--json", action="store_true", help="emit machine-readable output")

    subparsers.add_parser("run", help="start the Discord bot")

    migrate = subparsers.add_parser("migrate", help="apply database migrations up to a revision")
    migrate.add_argument(
        "--revision",
        default="head",
        help="target revision (default: head)",
    )

    invite = subparsers.add_parser(
        "invite", help="print the install URL for the configured application"
    )
    invite.add_argument(
        "--application-id",
        type=int,
        default=None,
        help="skip the API lookup and use this application id",
    )

    doctor = subparsers.add_parser(
        "doctor", help="run real checks against database, cache, providers and Discord"
    )
    doctor.add_argument("--json", action="store_true", help="emit machine-readable output")
    doctor.add_argument(
        "--skip-discord",
        action="store_true",
        help="skip the Discord identity check",
    )
    doctor.add_argument(
        "--skip-providers",
        action="store_true",
        help="skip the live model-catalogue checks against each AI provider",
    )
    doctor.add_argument(
        "--metrics",
        action="store_true",
        help="also print the metrics gathered during the run",
    )
    return parser


def _load_settings() -> Settings:
    try:
        return Settings()
    except PydanticValidationError as exc:
        missing = sorted(
            {str(error["loc"][0]) for error in exc.errors() if error.get("type") == "missing"}
        )
        detail = f": missing {', '.join(missing)}" if missing else ""
        raise TheSunError(
            f"configuration could not be loaded{detail}. {_MISSING_ENV_HINT}"
        ) from exc


def _print_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")


def _run_check_config(settings: Settings, *, as_json: bool) -> int:
    problems = validate_settings(settings)
    if as_json:
        _print_json(
            {
                "ok": not problems,
                "problems": problems,
                "default_provider": settings.default_provider,
                "providers": sorted(settings.ai.names()) if not problems else [],
            }
        )
    elif problems:
        sys.stderr.write("configuration problems:\n")
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
    else:
        sys.stdout.write("configuration is valid\n")
        sys.stdout.write(f"default provider: {settings.default_provider}\n")
        sys.stdout.write(f"providers: {', '.join(settings.ai.names())}\n")
    return EXIT_OK if not problems else EXIT_CONFIG_ERROR


async def _run_doctor(
    settings: Settings, *, as_json: bool, skip_discord: bool, skip_providers: bool
) -> int:
    context = build_service_context(settings)
    try:
        report: HealthReport = await HealthService(context).run(
            include_discord=not skip_discord, include_providers=not skip_providers
        )
        payload = report.to_dict()
        if as_json:
            _print_json(payload)
        else:
            _render_report(report)
        return EXIT_OK if report.ok else EXIT_CHECK_FAILED
    finally:
        await context.aclose()


def _render_report(report: HealthReport) -> None:
    for check in report.checks:
        status = "ok  " if check.ok else "FAIL"
        sys.stdout.write(
            f"[{status}] {check.name:<28} {check.latency_ms:7.1f} ms  {check.detail}\n"
        )
    if report.ok:
        sys.stdout.write("all checks passed\n")
    else:
        failed = ", ".join(check.name for check in report.failures)
        sys.stderr.write(f"failing checks: {failed}\n")


def _run_migrations(settings: Settings, *, revision: str) -> int:
    """Apply migrations against the configured database.

    The URL comes from settings, never from ``alembic.ini``, so credentials stay in
    the environment and the same code path serves a local run and a container.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    root = Path(__file__).resolve().parents[2]
    alembic_config = AlembicConfig(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "migrations"))
    alembic_config.attributes["database_url"] = settings.database.url
    sys.stdout.write(f"applying migrations to {settings.database.safe_url} (revision {revision})\n")
    command.upgrade(alembic_config, revision)
    sys.stdout.write("migrations applied\n")
    return EXIT_OK


async def _print_invite_url(settings: Settings, *, application_id: int | None) -> int:
    from the_sun.bot import invite_url
    from the_sun.integrations import fetch_application

    resolved = application_id
    if resolved is None:
        application = await fetch_application(settings.discord_token_value)
        resolved = application.id
        sys.stdout.write(f"application: {application.name} (id {application.id})\n")
    sys.stdout.write(f"invite URL:\n{invite_url(resolved)}\n")
    return EXIT_OK


def _render_metrics() -> None:
    sys.stdout.write("\n")
    sys.stdout.write(get_metrics().render_prometheus())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = _load_settings()
    except TheSunError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_CONFIG_ERROR

    configure_logging(settings.log_level, json_output=settings.log_json)

    try:
        if arguments.command == "check-config":
            return _run_check_config(settings, as_json=arguments.json)
        if arguments.command == "run":
            require_valid_settings(settings)
            from the_sun.bot import run_bot

            run_bot(settings)
            return EXIT_OK
        if arguments.command == "migrate":
            require_valid_settings(settings)
            return _run_migrations(settings, revision=arguments.revision)
        if arguments.command == "invite":
            require_valid_settings(settings)
            return asyncio.run(_print_invite_url(settings, application_id=arguments.application_id))
        if arguments.command == "doctor":
            # `doctor --json` is a diagnostic tool and reports real check failures
            # even from a broken configuration; the human-readable mode refuses to
            # run against a configuration it knows is unusable.
            if not arguments.json:
                require_valid_settings(settings)
            exit_code = asyncio.run(
                _run_doctor(
                    settings,
                    as_json=arguments.json,
                    skip_discord=arguments.skip_discord,
                    skip_providers=arguments.skip_providers,
                )
            )
            if arguments.metrics and not arguments.json:
                _render_metrics()
            return exit_code
    except TheSunError as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return EXIT_CONFIG_ERROR
    parser.error(f"unknown command {arguments.command!r}")
    return EXIT_CONFIG_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
