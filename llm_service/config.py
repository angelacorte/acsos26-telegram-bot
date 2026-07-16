"""Environment-driven configuration helpers shared across the service.

This module owns the small, side-effect-free helpers for reading typed values
from the environment and the paths that more than one module needs. Every other
module reads its own tuning knobs through these helpers so environment handling
lives in exactly one place.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# The bot and this service share the same conference dataset on disk.
DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "src/main/resources/acsos26/conference.json"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def parse_bool_env(name: str, default: bool) -> bool:
    """Read a boolean environment variable, treating 1/true/yes/on as true."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in _TRUE_VALUES


def parse_int_env(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to a safe default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using %s.", name, value, default)
        return default


def parse_float_env(name: str, default: float) -> float:
    """Read a float environment variable, falling back to a safe default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        LOGGER.warning("Invalid %s=%r; using %s.", name, value, default)
        return default
