"""Provider-neutral cloud interface.

The services layer depends only on this protocol, never on boto3 directly, so
an Azure or GCP provider can be added later (spec section 28) without touching
business logic or the API.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Dict, List


class CloudProvider(ABC):
    """Everything CloudGuard needs from a cloud account."""

    name: str = "unknown"

    @abstractmethod
    def list_resources(self) -> List[Dict[str, Any]]:
        """Compute inventory with utilisation and estimated cost attached."""

    @abstractmethod
    def get_metrics(self, hours: int = 24, period_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Time-series metrics for the monitored resources."""

    @abstractmethod
    def get_cost_and_usage(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Daily per-service cost records over [start, end]."""

    @abstractmethod
    def get_security_records(self, inject_extra_anomaly: bool = False) -> List[Dict[str, Any]]:
        """Raw behavioural observation windows, unlabelled and unscored."""

    def capabilities(self) -> Dict[str, Any]:
        """Describe which calls are live vs synthesised, for the health endpoint."""
        return {"provider": self.name}


class CloudProviderError(RuntimeError):
    """Raised when a cloud call fails in a way the caller should surface."""

    def __init__(self, message: str, *, service: str = "", retryable: bool = True):
        super().__init__(message)
        self.service = service
        self.retryable = retryable
