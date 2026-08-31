"""Policy configuration loader with validation."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ValidationPolicies(BaseModel):
    """Validation-related policy configuration."""

    high_value_threshold: float = 10000
    single_item_high_value_threshold: float = 50000
    accepted_currencies: list[str] = Field(default_factory=lambda: ["USD"])
    suspicious_vendor_keywords: list[str] = Field(
        default_factory=lambda: ["fraud", "fake", "scam"]
    )
    urgency_pressure_keywords: list[str] = Field(
        default_factory=lambda: ["urgent", "immediately", "wire transfer", "penalty", "asap"]
    )


class ApprovalPolicies(BaseModel):
    """Approval-related policy configuration."""

    max_critique_rounds: int = 3
    hard_reject_flags: list[str] = Field(
        default_factory=lambda: [
            "unknown_item", "stock_exceeded", "zero_stock", "negative_qty",
            "fraud_suspect", "blacklisted_vendor", "missing_vendor"
        ]
    )
    human_review_flags: list[str] = Field(
        default_factory=lambda: ["foreign_currency", "duplicate_invoice"]
    )
    conditional_approval_flags: list[str] = Field(
        default_factory=lambda: ["fuzzy_match", "high_value", "amount_mismatch"]
    )


class PaymentMethod(BaseModel):
    """Payment method threshold configuration."""

    max_amount: float | None = None
    method: str


class PaymentPolicies(BaseModel):
    """Payment-related policy configuration."""

    min_processing_delay_days: int = 1
    max_auto_payment: float = 25000
    payment_methods: list[PaymentMethod] = Field(default_factory=list)


class RiskThresholds(BaseModel):
    """Risk level thresholds."""

    low: int = 0
    medium: int = 25
    high: int = 50
    critical: int = 100


class RiskPolicies(BaseModel):
    """Risk scoring policy configuration."""

    flag_weights: dict[str, int] = Field(default_factory=dict)
    thresholds: RiskThresholds = Field(default_factory=RiskThresholds)


class Policies(BaseModel):
    """Root policy configuration."""

    version: str = "1.0"
    validation: ValidationPolicies = Field(default_factory=ValidationPolicies)
    approval: ApprovalPolicies = Field(default_factory=ApprovalPolicies)
    payment: PaymentPolicies = Field(default_factory=PaymentPolicies)
    risk: RiskPolicies = Field(default_factory=RiskPolicies)


def _find_policies_file() -> Path:
    """Find the policies.yaml file, checking multiple locations."""
    # Check env var first
    if env_path := os.getenv("POLICIES_PATH"):
        return Path(env_path)

    # Check relative to current working directory
    candidates = [
        Path("data/policies.yaml"),
        Path("../data/policies.yaml"),
        Path(__file__).parent.parent.parent / "data" / "policies.yaml",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Return default path (will use defaults if not found)
    return Path("data/policies.yaml")


def _load_policies_from_file(path: Path) -> dict[str, Any]:
    """Load policies from YAML file."""
    if not path.exists():
        return {}

    with open(path) as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_policies() -> Policies:
    """
    Get the current policy configuration.

    Loads from data/policies.yaml on first call, then caches.
    Falls back to defaults if file not found.

    Returns:
        Validated Policies instance.
    """
    path = _find_policies_file()
    data = _load_policies_from_file(path)
    return Policies.model_validate(data)


def reload_policies() -> Policies:
    """
    Force reload policies from disk.

    Useful after modifying the policies file.

    Returns:
        Fresh Policies instance.
    """
    get_policies.cache_clear()
    return get_policies()
