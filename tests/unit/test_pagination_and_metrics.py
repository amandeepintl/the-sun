"""Cursor pagination and metrics rendering."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from the_sun.errors import InvalidInputError
from the_sun.observability import MetricsRegistry
from the_sun.repositories import Page, decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    identifier = uuid.uuid4()
    decoded = decode_cursor(encode_cursor(moment, identifier))
    assert decoded.created_at == moment
    assert decoded.identifier == str(identifier)


def test_cursor_is_utc_normalised() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC) + timedelta(hours=2)
    decoded = decode_cursor(encode_cursor(moment, 7))
    assert decoded.created_at.utcoffset() == timedelta(0)
    assert decoded.created_at == moment


@pytest.mark.parametrize("cursor", ["not-base64!", "", "Zm9vYmFy"])
def test_invalid_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(InvalidInputError):
        decode_cursor(cursor)


def test_page_reports_whether_more_rows_exist() -> None:
    assert Page(items=[1, 2], next_cursor=None, limit=2).has_more is False
    assert Page(items=[1, 2], next_cursor="abc", limit=2).has_more is True


def test_counters_accumulate_per_label_set() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("sun_events_total", "Events.")
    counter.increment(outcome="ok")
    counter.increment(outcome="ok")
    counter.increment(outcome="error")
    rendered = registry.render_prometheus()
    assert 'sun_events_total{outcome="ok"} 2.0' in rendered
    assert 'sun_events_total{outcome="error"} 1.0' in rendered
    assert "# TYPE sun_events_total counter" in rendered


def test_gauges_and_histograms_render() -> None:
    registry = MetricsRegistry()
    registry.gauge("sun_queue_depth", "Depth.").set(4, shard="0")
    histogram = registry.histogram("sun_latency_seconds", "Latency.", buckets=(0.1, 1.0))
    histogram.observe(0.05, command="ask")
    histogram.observe(0.5, command="ask")
    histogram.observe(2.0, command="ask")

    rendered = registry.render_prometheus()
    assert 'sun_queue_depth{shard="0"} 4' in rendered
    assert 'sun_latency_seconds_bucket{command="ask",le="0.1"} 1' in rendered
    assert 'sun_latency_seconds_bucket{command="ask",le="1.0"} 2' in rendered
    assert 'sun_latency_seconds_bucket{command="ask",le="+Inf"} 3' in rendered
    assert 'sun_latency_seconds_count{command="ask"} 3' in rendered
    assert 'sun_latency_seconds_sum{command="ask"} 2.55' in rendered


def test_registering_a_metric_twice_with_another_type_fails() -> None:
    registry = MetricsRegistry()
    registry.counter("sun_same_name_total", "Counter.")
    with pytest.raises(TypeError):
        registry.gauge("sun_same_name_total", "Gauge.")


def test_registering_the_same_metric_twice_returns_the_same_object() -> None:
    registry = MetricsRegistry()
    assert registry.counter("sun_once_total", "Once.") is registry.counter(
        "sun_once_total", "Once."
    )
