"""Rintel-owned evidence authority seam.

The public package intentionally exposes no raw canonical reconciliation
function. Authority assignment comes only from the versioned lane registry.
"""

from .model import (
    AnnotationKind,
    AnnotationPayload,
    AuthorityClass,
    ClaimPayload,
    CanonicalStateRef,
    EnvelopeValidationError,
    EvidenceEnvelope,
)
from .engine import (
    AdmissionDenied,
    AlignmentKind,
    AlignmentResult,
    CanonicalAdmission,
    DEFAULT_ENGINE,
    EvidenceAuthorityEngine,
    RULE_VERSION,
)
from .registry import (
    DEFAULT_REGISTRY,
    LaneDefinition,
    LaneRegistry,
    UnknownLaneError,
)
from .serialization import envelope_from_wire
from .bridge import (
    BridgeKind,
    CrossLanguageBridge,
    InsufficientBridgeEvidence,
    validate_bridge,
)
from .query import AnswerStatus, QueryAuthorityResult, classify_evidence

__all__ = [
    "AdmissionDenied", "AlignmentKind", "AlignmentResult", "AnswerStatus",
    "AnnotationKind",
    "AnnotationPayload", "AuthorityClass", "BridgeKind", "CanonicalAdmission",
    "CanonicalStateRef", "ClaimPayload", "DEFAULT_ENGINE",
    "CrossLanguageBridge",
    "DEFAULT_REGISTRY", "EnvelopeValidationError", "EvidenceEnvelope",
    "EvidenceAuthorityEngine", "LaneDefinition", "LaneRegistry",
    "QueryAuthorityResult", "RULE_VERSION",
    "InsufficientBridgeEvidence", "UnknownLaneError", "envelope_from_wire",
    "classify_evidence", "validate_bridge",
]
