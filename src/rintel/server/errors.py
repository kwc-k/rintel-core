"""Unified API error envelope (SPEC-P1 §7: `{"error": {code, message, details}}`)."""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def envelope(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}


def repo_not_found(repo_id: str) -> ApiError:
    return ApiError(404, "repo_not_found",
                    f"repository '{repo_id}' not found")


def repo_not_indexed(repo_id: str) -> ApiError:
    return ApiError(404, "repo_not_indexed",
                    f"repository '{repo_id}' has no snapshot; run index first")


def snapshot_not_found(sid: str) -> ApiError:
    return ApiError(404, "snapshot_not_found",
                    f"snapshot '{sid}' not found")


# -- architecture plane (S3, SPEC-P1 §7-13..22) --------------------------
def workspace_not_found(workspace_id: str) -> ApiError:
    return ApiError(404, "workspace_not_found",
                    f"workspace '{workspace_id}' not found")


def model_not_found(model_id: str) -> ApiError:
    return ApiError(404, "model_not_found",
                    f"model '{model_id}' not found")


def component_not_found(component_id: str) -> ApiError:
    return ApiError(404, "component_not_found",
                    f"component '{component_id}' not found")


def relation_not_found(relation_id: str) -> ApiError:
    return ApiError(404, "relation_not_found",
                    f"relation '{relation_id}' not found")


def mapping_not_found(mapping_id: str) -> ApiError:
    return ApiError(404, "mapping_not_found",
                    f"mapping '{mapping_id}' not found")


# -- design plane (S4, SPEC-P1 §7-24/26/27 S4 subset) ---------------------
def not_a_proposal(model_id: str) -> ApiError:
    return ApiError(422, "not_a_proposal",
                    f"model '{model_id}' is not a proposal; fork it from the"
                    " AS-IS model first (it carries no frozen baseline)")


def invalid_model_kind(kind: str) -> ApiError:
    return ApiError(422, "invalid_model_kind",
                    f"model kind '{kind}' is not forkable; only"
                    " kind='proposal' is accepted here")


def invalid_change(unknown: list[str]) -> ApiError:
    return ApiError(422, "invalid_change",
                    "one or more change_ids do not exist in the current"
                    " diff", {"unknown": unknown})
