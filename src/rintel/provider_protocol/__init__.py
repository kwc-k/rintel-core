"""Rintel Provider Protocol v1 public interface."""

from .model import (
    PROTOCOL_VERSION,
    AnalysisContract,
    BuildContext,
    IncrementalCapability,
    ProviderAdvertisement,
    PublicationPolicy,
)
from .validation import ProtocolError, load_protocol_schema, validate_message
from .host import CancellationToken, HostRunResult, HostRunStatus, ProviderHost
from .legacy import LegacyCandidateAdapter

__all__ = [
    "PROTOCOL_VERSION", "AnalysisContract", "BuildContext",
    "IncrementalCapability", "ProviderAdvertisement", "PublicationPolicy",
    "ProtocolError", "load_protocol_schema", "validate_message",
    "HostRunResult", "HostRunStatus", "ProviderHost",
    "CancellationToken",
    "LegacyCandidateAdapter",
]
