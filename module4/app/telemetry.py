"""OpenTelemetry setup for Order Tracker: metrics, logs and traces.

``OTEL_EXPORTER`` chooses where the signals go:

- ``console`` (default): printed to stdout, readable with
  ``docker compose logs app``.
- ``otlp``: sent to an OpenTelemetry Collector at
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` (for example ``http://otel-collector:4318``).
- ``none``: nothing is exported; used by the tests.

What is recorded:

- **Metrics.** ``http.server.request.duration``, a histogram from the FastAPI
  instrumentation with the route template, method and status code as
  attributes; and ``order.lookups``, a counter for ``GET /api/orders/{id}``
  with the route, status code and outcome. Never the order id: metric
  attributes stay low-cardinality.
- **Traces.** One span per request (FastAPI), an ``order.lookup`` span around
  the lookup that carries the order id and any exception, and a
  ``SELECT orders`` span for its query. (The sqlite3 auto-instrumentation is
  not used: the app calls ``connection.execute()``, whose cursor is created
  in C, so that instrumentation never sees the queries.)
- **Logs.** Every ``logging`` record, including uvicorn's, with the current
  trace and span ids attached, so a log line leads to its trace.

Request headers and bodies are never captured.
"""

from __future__ import annotations

import logging
import os

# Use the stable HTTP semantic conventions (http.route,
# http.response.status_code, durations in seconds). Must be set before the
# instrumentation packages are imported.
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

from opentelemetry import metrics, trace  # noqa: E402
from opentelemetry._logs import set_logger_provider  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler  # noqa: E402
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader  # noqa: E402
from opentelemetry.sdk.resources import Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter  # noqa: E402

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "order-tracker")
SERVICE_VERSION = os.getenv("ORDER_TRACKER_VERSION", "local")
LOOKUP_ROUTE = "/api/orders/{order_id}"

tracer = trace.get_tracer("order_tracker")
meter = metrics.get_meter("order_tracker")
order_lookups = meter.create_counter(
    "order.lookups",
    unit="{lookup}",
    description="Order lookups by route, HTTP status code and outcome",
)


def record_lookup(status_code: int, outcome: str) -> None:
    order_lookups.add(
        1,
        {"http.route": LOOKUP_ROUTE, "http.response.status_code": status_code, "outcome": outcome},
    )


def _exporters(kind: str):
    if kind == "console":
        return ConsoleSpanExporter(), ConsoleMetricExporter(), ConsoleLogExporter()
    if kind == "otlp":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Each exporter derives its path from OTEL_EXPORTER_OTLP_ENDPOINT.
        return OTLPSpanExporter(), OTLPMetricExporter(), OTLPLogExporter()
    raise ValueError(f"OTEL_EXPORTER must be console, otlp or none, not {kind!r}")


def configure(app) -> None:
    """Install the providers and instrument ``app``. Safe to call once."""

    kind = os.getenv("OTEL_EXPORTER", "console").lower()
    if kind == "none":
        return

    resource = Resource.create({"service.name": SERVICE_NAME, "service.version": SERVICE_VERSION})
    span_exporter, metric_exporter, log_exporter = _exporters(kind)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # The reader honours OTEL_METRIC_EXPORT_INTERVAL (milliseconds).
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(metric_exporter)])
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)
    root = logging.getLogger()
    root.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    # The Compose health check polls /healthz every few seconds; keep it out.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
