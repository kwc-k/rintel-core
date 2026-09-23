"""SOFTWARE-LVS1 comparators (per-relation modules, spec §27)."""
from ._impl import (
    compare_blocks, compare_boundaries, compare_calls, compare_control,
    compare_data, compare_ports, compare_resources, compare_timing,
)

ALL_COMPARATORS = [
    compare_blocks, compare_ports, compare_calls, compare_data,
    compare_resources, compare_boundaries, compare_control, compare_timing,
]
