from io import StringIO

from app.utils.perf_metrics import CloudWatchEmbeddedMetricSink, InMemoryPerfMetrics


def test_export_prometheus_renders_counters_and_latency_summaries():
    metrics = InMemoryPerfMetrics()
    metrics.increment("webhook_delivery_success", by=3)
    metrics.observe_ms("dispatch_batch_latency_ms", 100)
    metrics.observe_ms("dispatch_batch_latency_ms", 200)

    output = metrics.export_prometheus(namespace="lastmile")

    assert "# Generated from in-memory backend runtime metrics." in output
    assert "lastmile_webhook_delivery_success_total 3" in output
    assert "lastmile_dispatch_batch_latency_ms_count 2" in output
    assert "lastmile_dispatch_batch_latency_ms_avg 150" in output
    assert "lastmile_dispatch_batch_latency_ms_median 150" in output
    assert "lastmile_dispatch_batch_latency_ms_p50 100" in output
    assert "lastmile_dispatch_batch_latency_ms_p95 200" in output


def test_export_prometheus_sanitizes_metric_and_namespace_names():
    metrics = InMemoryPerfMetrics()
    metrics.increment("Webhook Delivery Success", by=1)

    output = metrics.export_prometheus(namespace="Last Mile API")

    assert "last_mile_api_webhook_delivery_success_total 1" in output


def test_cloudwatch_emf_sink_emits_raw_json_lines():
    stream = StringIO()
    sink = CloudWatchEmbeddedMetricSink(
        namespace="LastMile/Application",
        application="last-mile-delivery",
        environment="staging",
        service="api",
        stream=stream,
        now_ms=lambda: 1_710_000_000_000,
    )

    sink.emit("Dispatch Batch Latency Ms", 123.4, unit="Milliseconds")

    line = stream.getvalue().strip()
    assert '"Namespace":"LastMile/Application"' in line
    assert '"Application":"last-mile-delivery"' in line
    assert '"Environment":"staging"' in line
    assert '"Service":"api"' in line
    assert '"dispatch_batch_latency_ms":123.4' in line


def test_in_memory_metrics_can_emit_cloudwatch_samples():
    stream = StringIO()
    sink = CloudWatchEmbeddedMetricSink(
        namespace="LastMile/Application",
        application="last-mile-delivery",
        environment="production",
        service="worker",
        stream=stream,
        now_ms=lambda: 1_710_000_000_000,
    )
    metrics = InMemoryPerfMetrics(metric_sink=sink)

    metrics.increment("webhook_delivery_dead_letter", by=2)
    metrics.observe_ms("webhook_delivery_latency_ms", 75)

    lines = [line for line in stream.getvalue().strip().splitlines() if line]
    assert len(lines) == 2
    assert '"webhook_delivery_dead_letter":2' in lines[0]
    assert '"Unit":"Count"' in lines[0]
    assert '"webhook_delivery_latency_ms":75.0' in lines[1]
    assert '"Unit":"Milliseconds"' in lines[1]
