from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from statistics import median
from typing import Callable, Protocol, TextIO

from app.config import settings

_METRIC_NAME_SANITIZER = re.compile(r"[^a-zA-Z0-9_]+")


def _sanitize_metric_name(name: str) -> str:
    sanitized = _METRIC_NAME_SANITIZER.sub("_", name).strip("_").lower()
    return sanitized or "unnamed_metric"


def _format_metric_value(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    formatted = f"{value:.6f}".rstrip("0").rstrip(".")
    return formatted or "0"


class MetricSink(Protocol):
    def emit(
        self,
        metric_name: str,
        value: float | int,
        *,
        unit: str,
    ) -> None: ...


class CloudWatchEmbeddedMetricSink:
    def __init__(
        self,
        *,
        namespace: str,
        application: str,
        environment: str,
        service: str,
        stream: TextIO | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._namespace = namespace
        self._application = application
        self._environment = environment
        self._service = service
        self._stream = stream or sys.stdout
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()

    def emit(
        self,
        metric_name: str,
        value: float | int,
        *,
        unit: str,
    ) -> None:
        sanitized_name = _sanitize_metric_name(metric_name)
        payload = {
            "_aws": {
                "Timestamp": int(self._now_ms()),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self._namespace,
                        "Dimensions": [["Application", "Environment", "Service"]],
                        "Metrics": [
                            {
                                "Name": sanitized_name,
                                "Unit": unit,
                            }
                        ],
                    }
                ],
            },
            "Application": self._application,
            "Environment": self._environment,
            "Service": self._service,
            sanitized_name: value,
        }
        encoded = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            self._stream.write(encoded + "\n")
            self._stream.flush()


class InMemoryPerfMetrics:
    """Small in-memory metrics helper for timing/counter telemetry."""

    def __init__(
        self,
        *,
        max_samples_per_metric: int = 500,
        metric_sink: MetricSink | None = None,
    ) -> None:
        self._max_samples = max_samples_per_metric
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._max_samples)
        )
        self._counters: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._metric_sink = metric_sink

    def observe_ms(self, metric_name: str, value_ms: float) -> None:
        numeric_value = float(value_ms)
        with self._lock:
            self._samples[metric_name].append(numeric_value)
        if self._metric_sink is not None:
            self._metric_sink.emit(metric_name, numeric_value, unit="Milliseconds")

    def increment(self, counter_name: str, by: int = 1) -> None:
        numeric_value = int(by)
        with self._lock:
            self._counters[counter_name] += numeric_value
        if self._metric_sink is not None:
            self._metric_sink.emit(counter_name, numeric_value, unit="Count")

    def get_counter(self, counter_name: str) -> int:
        with self._lock:
            return int(self._counters.get(counter_name, 0))

    def _get_values(self, metric_name: str) -> list[float]:
        with self._lock:
            return list(self._samples.get(metric_name, []))

    @staticmethod
    def _percentile_from_sorted_values(values: list[float], q: float) -> float | None:
        if not values:
            return None
        if q <= 0:
            return values[0]
        if q >= 1:
            return values[-1]
        index = int(round(q * (len(values) - 1)))
        return values[index]

    @classmethod
    def _summarize_values(cls, values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"count": 0, "p50_ms": None, "p95_ms": None, "avg_ms": None}
        values.sort()
        return {
            "count": len(values),
            "p50_ms": cls._percentile_from_sorted_values(values, 0.5),
            "p95_ms": cls._percentile_from_sorted_values(values, 0.95),
            "avg_ms": sum(values) / len(values),
            "median_ms": median(values),
        }

    def percentile(self, metric_name: str, q: float) -> float | None:
        values = self._get_values(metric_name)
        values.sort()
        return self._percentile_from_sorted_values(values, q)

    def summary(self, metric_name: str) -> dict[str, float | int | None]:
        return self._summarize_values(self._get_values(metric_name))

    def export_prometheus(self, *, namespace: str = "lastmile") -> str:
        with self._lock:
            counter_snapshot = dict(self._counters)
            sample_snapshot = {
                metric_name: list(values)
                for metric_name, values in self._samples.items()
            }

        prefix = _sanitize_metric_name(namespace)
        lines = ["# Generated from in-memory backend runtime metrics."]

        for counter_name in sorted(counter_snapshot):
            metric_name = _sanitize_metric_name(counter_name)
            value = counter_snapshot[counter_name]
            lines.append(f"{prefix}_{metric_name}_total {_format_metric_value(value)}")

        for sample_name in sorted(sample_snapshot):
            metric_name = _sanitize_metric_name(sample_name)
            summary = self._summarize_values(sample_snapshot[sample_name])
            lines.append(
                f"{prefix}_{metric_name}_count "
                f"{_format_metric_value(summary['count'])}"
            )
            for suffix, key in (
                ("avg", "avg_ms"),
                ("median", "median_ms"),
                ("p50", "p50_ms"),
                ("p95", "p95_ms"),
            ):
                value = summary.get(key)
                if value is not None:
                    lines.append(
                        f"{prefix}_{metric_name}_{suffix} "
                        f"{_format_metric_value(value)}"
                    )

        return "\n".join(lines) + "\n"
_metric_sink: MetricSink | None = None
if settings.EMF_METRICS_ENABLED:
    _metric_sink = CloudWatchEmbeddedMetricSink(
        namespace=settings.EMF_METRICS_NAMESPACE,
        application=settings.EMF_METRICS_APPLICATION,
        environment=settings.ENVIRONMENT,
        service=settings.EMF_METRICS_SERVICE,
    )

perf_metrics = InMemoryPerfMetrics(metric_sink=_metric_sink)
