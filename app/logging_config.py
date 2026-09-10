import logging
import sys


def setup_logging(app) -> None:
    log_level_name = app.config.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(log_level)

    root = logging.getLogger()
    root.setLevel(log_level)
    # Avoid duplicate handlers if factory is called multiple times (e.g. in tests)
    if not root.handlers:
        root.addHandler(handler)

    # Suppress noisy low-value libraries
    for noisy in ("httpx", "httpcore", "urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    app.logger.info("Logging ready — level=%s", log_level_name)
