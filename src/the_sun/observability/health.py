"""Optional HTTP endpoints for orchestrators.

Enabled with ``METRICS_ENABLED=true``. ``/healthz`` is a pure liveness check with
no dependency calls, ``/readyz`` performs real database and cache round trips so a
container is only marked ready when it can actually serve, and ``/metrics`` serves
the Prometheus text format.
"""

from __future__ import annotations

import logging

from aiohttp import web

from the_sun.services import HealthReport, HealthService, ServiceContext

__all__ = ["HealthServer", "build_app"]

logger = logging.getLogger(__name__)


def build_app(context: ServiceContext) -> web.Application:
    """Build the aiohttp application serving the health routes."""
    service = HealthService(context)

    async def healthz(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def readyz(_request: web.Request) -> web.Response:
        checks = [
            service.check_config(),
            await service.check_database(),
            await service.check_cache(),
        ]
        report = HealthReport(checks=checks)
        return web.json_response(report.to_dict(), status=200 if report.ok else 503)

    async def metrics(_request: web.Request) -> web.Response:
        from the_sun.observability import get_metrics

        return web.Response(text=get_metrics().render_prometheus(), content_type="text/plain")

    async def livez(_request: web.Request) -> web.Response:
        return web.json_response({"status": "alive"})

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/livez", livez)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/metrics", metrics)
    return app


class HealthServer:
    """Runs the health application for the lifetime of the process."""

    def __init__(self, context: ServiceContext, *, host: str, port: int) -> None:
        self._app = build_app(context)
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def bound_port(self) -> int:
        """The port actually bound, which matters when the configured one is 0."""
        if self._site is None:
            return self._port
        server = getattr(self._site, "_server", None)
        if server is None or not server.sockets:
            return self._port
        return int(server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info(
            "health endpoint listening",
            extra={"host": self._host, "port": self.bound_port},
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
