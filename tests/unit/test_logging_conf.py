import io
import logging

from app.logging_conf import _RedactSecretsFilter


def test_redacts_api_secret_from_propagated_child_logger_records():
    """A filter on the root logger itself would NOT catch this — logger
    filters are skipped for records received via propagation from a
    child logger, only a handler-level filter runs regardless of the
    record's origin. Reproduces that propagation path directly."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(_RedactSecretsFilter())

    root = logging.getLogger()
    original_handlers = root.handlers
    original_level = root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        child_logger = logging.getLogger("some_http_client")
        child_logger.info(
            'HTTP Request: %s %s "%s %d %s"',
            "POST",
            "https://example.com/collect?measurement_id=G-TEST&api_secret=SUPERSECRET123",
            "HTTP/1.1", 204, "No Content",
        )
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    output = stream.getvalue()
    assert "SUPERSECRET123" not in output
    assert "api_secret=<redacted>" in output
    assert "measurement_id=G-TEST" in output  # not sensitive, stays visible


def test_redaction_only_targets_api_secret_param():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(_RedactSecretsFilter())

    root = logging.getLogger()
    original_handlers = root.handlers
    original_level = root.level
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        logging.getLogger("test").info("Nothing sensitive here, just a normal log line")
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    assert stream.getvalue().strip() == "Nothing sensitive here, just a normal log line"
