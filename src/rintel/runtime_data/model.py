"""RUNTIME-DATA0 model: runtime data objects over the canonical kernel.

Bound to existing kernel Data Symbols / Values / Locations — never a second
canonical Data layer.  Shape_status uses RESOLVED (runtime witness) / SYMBOLIC /
PARTIAL / UNKNOWN; static facts are never overwritten.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RuntimeDataObject:
    runtime_data_id: str
    run_id: str
    canonical_symbol_id: str
    canonical_location_id: str
    dtype: str                 # float64 / int32
    rank: int
    shape: list                # [] for scalar
    shape_status: str          # RESOLVED (runtime witness)
    element_count: int
    byte_size: int             # resident extent (bytes)
    logical_size: int          # dtype_bits * element_count; NEVER "bytes_transferred"
    allocation_identity: str   # raw pointer / "static"
    truth_class: str = "OBSERVED"
    coverage: str = "COMPLETE"  # COMPLETE / PARTIAL (honest per-region)
    scope: str = "STATE"        # STATE (shared buffer) / PARAM (through port) / LOCAL
    witness: dict = field(default_factory=dict)
    producer_event_ids: list = field(default_factory=list)
    consumer_event_ids: list = field(default_factory=list)


@dataclass
class RuntimeLocation:
    location_id: str
    run_id: str
    canonical_symbol_id: str
    region: str                # matrix / x / a / b / rex / ipiv / nb-state / info
    offset_bytes: int          # from allocation base
    extent_bytes: int
    allocation_identity: str
    version: int = 1
    created_event_id: Optional[str] = None
    freed_event_id: Optional[str] = None
    static_description: str = ""
    note: str = ""


@dataclass
class DataAccessEvent:
    event_id: str
    run_id: str
    invocation_id: str
    operation_kind: str        # READ / WRITE / READ_WRITE / ALLOCATE / FREE / BIND
    runtime_data_id: str
    canonical_location_id: str
    ts_ns: int
    order: int
    source_line: Optional[int] = None
    byte_extent: int = 0
    note: str = ""


@dataclass
class RuntimeBinding:
    binding_id: str
    run_id: str
    call_invocation_id: str
    caller_object_id: str      # runtime data object (actual)
    formal_symbol_id: str      # callee formal (kernel symbol id)
    callee_symbol_id: str      # callee callable symbol id
    position: int
    truth: str = "EXACT"
    evidence: dict = field(default_factory=dict)
