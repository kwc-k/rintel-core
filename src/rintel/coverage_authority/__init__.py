"""Rintel-owned, exact-domain Clang absence authority."""
from .model import CAPABILITY_REGISTRY, CoverageCertificate, ISSUER_ID, RULE_VERSION
from .issuer import CoverageAuthority, CoverageUnavailable
from .validation import applicable_complete

__all__ = ["CAPABILITY_REGISTRY", "CoverageAuthority", "CoverageCertificate",
           "CoverageUnavailable", "ISSUER_ID", "RULE_VERSION",
           "applicable_complete"]
