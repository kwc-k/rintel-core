"""Shared fail-closed response for retired pre-lifecycle design writes."""
from __future__ import annotations

from typing import NoReturn

from ..errors import ApiError


def legacy_design_write_deprecated() -> NoReturn:
    raise ApiError(
        410,
        "legacy_design_write_deprecated",
        "this design write route is deprecated; use an existing DesignChange",
        {
            "deprecated": True,
            "replacement": "/api/v1/design-changes/{change_id}/commands",
            "required_change_context": "change_id",
        },
    )
