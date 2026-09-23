"""Host-owned BUILD/TEST registry shared by REST, MCP, and Build Recovery."""
from __future__ import annotations

import json
from typing import Any

from ..build_recovery import BuildProfile
from ..execution_authority import ExecutionAuthority, ExecutionError, ExecutionProfile


def _tuple_fields(raw: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    item = dict(raw)
    for name in names:
        if name in item:
            item[name] = tuple(item[name])
    return item


def load_execution_registry(settings: Any) -> tuple[tuple[BuildProfile, ...], ExecutionAuthority]:
    """Only server configuration can define argv, executable, cwd, and policy."""
    try:
        legacy = json.loads(settings.build_profiles_json)
        additional = json.loads(getattr(settings, "execution_profiles_json", "[]"))
        if not isinstance(legacy, list) or not isinstance(additional, list):
            raise ExecutionError("profile registry must be lists")
        builds = tuple(BuildProfile(**_tuple_fields(item, (
            "command", "source_files", "defines", "include_paths", "module_paths",
            "libraries", "artifact_files"))) for item in legacy)
        profiles = [profile.execution_profile() for profile in builds]
        for raw in additional:
            item = _tuple_fields(raw, ("argv", "input_files", "artifact_files",
                                       "environment_keys"))
            if "parameter_choices" in item:
                choices = item["parameter_choices"]
                if not isinstance(choices, dict):
                    raise ExecutionError("parameter_choices must be finite mapping")
                item["parameter_choices"] = tuple(
                    (name, tuple(values)) for name, values in choices.items())
            profiles.append(ExecutionProfile(**item))
        authority = ExecutionAuthority(
            getattr(settings, "execution_artifacts_path",
                    str(settings.build_artifacts_path) + "/execution"),
            tuple(profiles))
        return builds, authority
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ExecutionError("host execution profile configuration invalid") from exc
