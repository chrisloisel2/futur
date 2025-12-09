"""Structured logging configuration with metrics."""
import logging
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import json_log_formatter
    HAS_JSON_FORMATTER = True
except ImportError:
    HAS_JSON_FORMATTER = False


class MetricsCollector:
    """Collect and track pipeline metrics."""

    def __init__(self) -> None:
        self.metrics: Dict[str, Any] = {
            "api_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
            "data_rows_processed": 0,
            "execution_times": {},
        }

    def increment(self, metric: str, value: int = 1) -> None:
        """Increment a counter metric."""
        if metric in self.metrics:
            self.metrics[metric] += value
        else:
            self.metrics[metric] = value

    def record_time(self, operation: str, duration: float) -> None:
        """Record execution time for an operation."""
        if operation not in self.metrics["execution_times"]:
            self.metrics["execution_times"][operation] = []
        self.metrics["execution_times"][operation].append(duration)

    @contextmanager
    def timer(self, operation: str):
        """Context manager for timing operations."""
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            self.record_time(operation, duration)

    def get_cache_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.metrics["cache_hits"] + self.metrics["cache_misses"]
        if total == 0:
            return 0.0
        return self.metrics["cache_hits"] / total

    def get_average_time(self, operation: str) -> Optional[float]:
        """Get average execution time for operation."""
        times = self.metrics["execution_times"].get(operation)
        if not times:
            return None
        return sum(times) / len(times)

    def summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        summary = self.metrics.copy()
        summary["cache_hit_rate"] = self.get_cache_hit_rate()

        # Calculate averages for execution times
        summary["avg_execution_times"] = {
            op: self.get_average_time(op)
            for op in self.metrics["execution_times"]
        }

        return summary

    def reset(self) -> None:
        """Reset all metrics."""
        self.metrics = {
            "api_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "errors": 0,
            "data_rows_processed": 0,
            "execution_times": {},
        }


# Global metrics instance
_metrics_instance = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Get global metrics collector."""
    return _metrics_instance


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add custom fields
        if hasattr(record, "metrics"):
            log_data["metrics"] = record.metrics

        if hasattr(record, "operation"):
            log_data["operation"] = record.operation

        if hasattr(record, "duration"):
            log_data["duration"] = record.duration

        return str(log_data)


def setup_logging(
    level: str = "INFO",
    log_format: str = "text",
    log_file: Optional[str] = None,
    rotation: Optional[str] = None,
) -> None:
    """
    Setup structured logging for the pipeline.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Format type ('json' or 'text')
        log_file: Path to log file (optional)
        rotation: Log rotation settings (not implemented yet)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatter
    if log_format == "json" and HAS_JSON_FORMATTER:
        formatter = json_log_formatter.JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


class MetricsLogger:
    """Logger with metrics tracking."""

    def __init__(self, name: str) -> None:
        self.logger = logging.getLogger(name)
        self.metrics = get_metrics()

    def log_api_call(self, endpoint: str, duration: float, success: bool) -> None:
        """Log API call with metrics."""
        self.metrics.increment("api_calls")
        self.metrics.record_time(f"api_call_{endpoint}", duration)

        if success:
            self.logger.info(f"API call to {endpoint} succeeded in {duration:.2f}s")
        else:
            self.metrics.increment("errors")
            self.logger.error(f"API call to {endpoint} failed after {duration:.2f}s")

    def log_cache_operation(self, key: str, hit: bool) -> None:
        """Log cache operation."""
        if hit:
            self.metrics.increment("cache_hits")
            self.logger.debug(f"Cache hit: {key}")
        else:
            self.metrics.increment("cache_misses")
            self.logger.debug(f"Cache miss: {key}")

    def log_data_processing(self, rows: int, operation: str, duration: float) -> None:
        """Log data processing operation."""
        self.metrics.increment("data_rows_processed", rows)
        self.metrics.record_time(operation, duration)

        self.logger.info(
            f"Processed {rows} rows in {duration:.2f}s ({rows/duration:.0f} rows/s)"
        )

    def log_metrics_summary(self) -> None:
        """Log current metrics summary."""
        summary = self.metrics.summary()
        self.logger.info(f"Metrics summary: {summary}")
