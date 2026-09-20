"""Metrics.

Deliberately dependency-free: a counter, a gauge and a histogram are enough to
answer the operational questions this bot has, and they render in the Prometheus
text format so any scraper can read them.

Observations are recorded from real work (health checks now, provider calls and
rate-limit decisions in later stages). Nothing here is estimated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = ["DEFAULT_BUCKETS", "MetricsRegistry", "get_metrics"]

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

Labels = tuple[tuple[str, str], ...]


def _format_labels(labels: Labels) -> str:
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{value}"' for key, value in labels)
    return f"{{{rendered}}}"


@dataclass
class Counter:
    """Monotonic counter."""

    name: str
    help: str
    values: dict[Labels, float] = field(default_factory=dict)

    def increment(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        self.values[key] = self.values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        lines.extend(
            f"{self.name}{_format_labels(labels)} {value}" for labels, value in self.values.items()
        )
        return lines


@dataclass
class Gauge:
    """Value that can go up or down."""

    name: str
    help: str
    values: dict[Labels, float] = field(default_factory=dict)

    def set(self, value: float, **labels: str) -> None:
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        self.values[key] = value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        lines.extend(
            f"{self.name}{_format_labels(labels)} {value}" for labels, value in self.values.items()
        )
        return lines


@dataclass
class Histogram:
    """Bucketed observations with sum and count."""

    name: str
    help: str
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    counts: dict[Labels, list[int]] = field(default_factory=dict)
    totals: dict[Labels, float] = field(default_factory=dict)
    observations: dict[Labels, int] = field(default_factory=dict)

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        counts = self.counts.setdefault(key, [0] * len(self.buckets))
        for index, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                counts[index] += 1
        self.totals[key] = self.totals.get(key, 0.0) + value
        self.observations[key] = self.observations.get(key, 0) + 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for labels, counts in self.counts.items():
            for index, upper_bound in enumerate(self.buckets):
                bucket_labels = (*labels, ("le", str(upper_bound)))
                lines.append(f"{self.name}_bucket{_format_labels(bucket_labels)} {counts[index]}")
            infinite_labels = (*labels, ("le", "+Inf"))
            lines.append(
                f"{self.name}_bucket{_format_labels(infinite_labels)} "
                f"{self.observations.get(labels, 0)}"
            )
            lines.append(f"{self.name}_sum{_format_labels(labels)} {self.totals.get(labels, 0.0)}")
            lines.append(
                f"{self.name}_count{_format_labels(labels)} {self.observations.get(labels, 0)}"
            )
        return lines


class MetricsRegistry:
    """Collects the metrics defined by the application."""

    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}

    def counter(self, name: str, help_text: str) -> Counter:
        metric = self._metrics.get(name)
        if metric is None:
            metric = Counter(name=name, help=help_text)
            self._metrics[name] = metric
        if not isinstance(metric, Counter):
            raise TypeError(f"metric {name!r} is already registered with another type")
        return metric

    def gauge(self, name: str, help_text: str) -> Gauge:
        metric = self._metrics.get(name)
        if metric is None:
            metric = Gauge(name=name, help=help_text)
            self._metrics[name] = metric
        if not isinstance(metric, Gauge):
            raise TypeError(f"metric {name!r} is already registered with another type")
        return metric

    def histogram(
        self, name: str, help_text: str, *, buckets: Iterable[float] | None = None
    ) -> Histogram:
        metric = self._metrics.get(name)
        if metric is None:
            metric = Histogram(
                name=name,
                help=help_text,
                buckets=tuple(buckets) if buckets is not None else DEFAULT_BUCKETS,
            )
            self._metrics[name] = metric
        if not isinstance(metric, Histogram):
            raise TypeError(f"metric {name!r} is already registered with another type")
        return metric

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for name in sorted(self._metrics):
            lines.extend(self._metrics[name].render())
        return "\n".join(lines) + "\n"


_metrics: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    """Return the process-wide metrics registry."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsRegistry()
    return _metrics
