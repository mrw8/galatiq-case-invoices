"""Structured JSON logging configuration."""

import json
import logging
import sys
from datetime import datetime
from typing import Any

import structlog


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    """
    Configure structured logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR).
        json_output: If True, output JSON logs. If False, use console format.
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    # Configure structlog processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]

    if json_output:
        # JSON output for production
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Console output for development
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


class AgentLogger:
    """
    Specialized logger for agent events.

    Ensures consistent event structure across all agents.
    Optionally adds events to pipeline state for tracing.
    """

    def __init__(self, agent_name: str, run_id: str, state: Any | None = None):
        self.agent_name = agent_name
        self.run_id = run_id
        self._logger = get_logger(agent_name)
        self._state = state  # Optional PipelineState to add events to

    def event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        """
        Log an agent event and return the event dict.

        Args:
            event_type: Type of event (e.g., "started", "completed", "error").
            data: Additional event data.
            level: Log level.

        Returns:
            The event dict that was logged.
        """
        event_data = {
            "run_id": self.run_id,
            "agent": self.agent_name,
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data or {},
        }

        log_method = getattr(self._logger, level, self._logger.info)
        log_method(event_type, **event_data)

        # Also add to pipeline state if available
        if self._state is not None and hasattr(self._state, "events"):
            self._state.events.append(event_data)

        return event_data

    def started(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Log agent started event."""
        return self.event("started", data)

    def completed(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Log agent completed event."""
        return self.event("completed", data)

    def error(self, error: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Log agent error event."""
        error_data = {"error": error, **(data or {})}
        return self.event("error", error_data, level="error")

    def retry(self, attempt: int, reason: str) -> dict[str, Any]:
        """Log a retry attempt."""
        return self.event("retry", {"attempt": attempt, "reason": reason}, level="warning")


def write_trace(run_id: str, events: list[dict], output_dir: str = "runs") -> str:
    """
    Write full trace log to file.

    Args:
        run_id: The run identifier.
        events: List of event dicts from the pipeline.
        output_dir: Directory to write traces to.

    Returns:
        Path to the written trace file.
    """
    import os

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"{run_id}.json")

    with open(filepath, "w") as f:
        json.dump(
            {
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
                "event_count": len(events),
                "events": events,
            },
            f,
            indent=2,
            default=str,
        )

    return filepath
