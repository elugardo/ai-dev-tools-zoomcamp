import os

# Keep telemetry out of the test output; the app code paths still run.
os.environ.setdefault("OTEL_EXPORTER", "none")
