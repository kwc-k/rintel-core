"""Server settings (SPEC-P1 §28: `RINTEL_DATABASE_URL` env contract)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
import os

from rintel.e2e_profile import load_e2e_profile

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINTEL_", extra="ignore")

    # PG is the product database; when unset the server runs on the SQLite
    # CLI/regression path (P0, SPEC-P1 §16 双轨并存).
    database_url: str | None = None          # RINTEL_DATABASE_URL
    pg_schema: str | None = None             # RINTEL_PG_SCHEMA (tests)
    sqlite_path: str = "evidence.db"         # RINTEL_SQLITE_PATH
    build_artifacts_path: str = str(REPO_ROOT / "analysis_tournament/build_recovery0/runtime")
    build_profiles_json: str = "[]"            # host-owned profiles; no public command input
    execution_artifacts_path: str = str(REPO_ROOT / "analysis_tournament/execution_authority0/runtime")
    execution_profiles_json: str = "[]"         # additional host-owned TEST profiles
    git_workspace_state_path: str = str(
        REPO_ROOT / "analysis_tournament/git_workspace_integration0/runtime")
    web_dist_path: str = str(REPO_ROOT / "web/dist")
    e2e_read_only: bool = False
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173")  # vite dev

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    profile = load_e2e_profile()
    if profile is None:
        return settings
    store_path = str(Path(profile["store"]["path"]).resolve())
    if os.environ.get("RINTEL_DATABASE_URL") or (
        os.environ.get("RINTEL_SQLITE_PATH")
        and str(Path(os.environ["RINTEL_SQLITE_PATH"]).resolve()) != store_path
    ):
        raise ValueError("E2E profile conflicts with independent datastore settings")
    artifacts = profile.get("artifacts", {})
    return settings.model_copy(update={
        "database_url": None, "sqlite_path": store_path,
        "build_artifacts_path": artifacts.get("build", settings.build_artifacts_path),
        "execution_artifacts_path": artifacts.get("execution", settings.execution_artifacts_path),
        "build_profiles_json": json.dumps(profile.get("build_profiles", [])),
        "execution_profiles_json": json.dumps(profile.get("execution_profiles", [])),
        "e2e_read_only": profile["store"].get("read_only", False),
    })
