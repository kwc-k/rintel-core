"""Local release startup and capability checks."""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path


def data_root() -> Path:
    if os.environ.get("RINTEL_DATA_HOME"):
        return Path(os.environ["RINTEL_DATA_HOME"]).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Rintel"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "rintel"


def configure_local_paths() -> Path:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    defaults = {
        "RINTEL_SQLITE_PATH": root / "evidence.db",
        "RINTEL_BUILD_ARTIFACTS_PATH": root / "build-artifacts",
        "RINTEL_EXECUTION_ARTIFACTS_PATH": root / "execution-artifacts",
        "RINTEL_GIT_WORKSPACE_STATE_PATH": root / "git-workspaces",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))
    return root


def _version(cmd: str) -> str | None:
    executable = shutil.which(cmd)
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True,
                                text=True, timeout=5, check=False)
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def capability_report() -> dict:
    """Report availability without treating optional tools as startup blockers."""
    git = _version("git")
    clang = _version("clang")
    postgres = bool(os.environ.get("RINTEL_DATABASE_URL"))
    node = _version("node")
    try:
        node_ready = node is not None and int(node.lstrip("v").split(".")[0]) >= 20
    except ValueError:
        node_ready = False
    pnpm = _version("pnpm")
    npx = _version("npx")
    pinned_pnpm = pnpm == "11.22.0"
    browser_root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", (
        str(Path.home() / "Library/Caches/ms-playwright") if sys.platform == "darwin"
        else str(Path.home() / ".cache/ms-playwright"))))
    web_root = Path(__file__).resolve().parents[2] / "web"
    system_chrome = (Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file()
                     if sys.platform == "darwin" else
                     bool(shutil.which("google-chrome") or shutil.which("chromium")))
    browser_ready = (web_root / "node_modules/@playwright/test").exists() and (
        any(browser_root.glob("chromium-*")) or system_chrome)
    git_repo_ready = False
    db_path = Path(os.environ.get("RINTEL_SQLITE_PATH", data_root() / "evidence.db"))
    if git and db_path.is_file():
        try:
            with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
                roots = [row[0] for row in conn.execute("SELECT root_path FROM repos")]
            git_repo_ready = any(subprocess.run(
                ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, timeout=5, check=False).stdout.strip() == "true"
                for root in roots)
        except (sqlite3.Error, OSError, subprocess.SubprocessError):
            pass
    build_profiles = os.environ.get("RINTEL_BUILD_PROFILES_JSON", "[]")
    execution_profiles = os.environ.get("RINTEL_EXECUTION_PROFILES_JSON", "[]")
    def item(available: bool, why: str, detail: str | None = None) -> dict:
        return {"status": "AVAILABLE" if available else "UNAVAILABLE",
                "why": why, "detail": detail}
    return {
        "platform": f"{platform.system()} {platform.machine()}",
        "required": {
            "python": item(sys.version_info >= (3, 12),
                           "Python 3.12+ runs the backend; ./install.sh installs the project environment",
                           sys.version.split()[0]),
            "node": item(node_ready, "Node 20+ builds the local UI during installation; install Node.js", node),
            "pnpm_or_npx": item(pinned_pnpm or bool(npx),
                                 "pnpm 11.22.0 installs locked UI dependencies; npx can fetch the pinned version",
                                 "pnpm 11.22.0" if pinned_pnpm else
                                 (f"npx {npx}; fetches pnpm 11.22.0" if npx else pnpm)),
        },
        "optional": {
            "git": item(bool(git), "Git enables repository and worktree operations; install Git for this workflow", git),
            "clang": item(bool(clang), "Compiler-grade C/C++ observations require Clang", clang),
            "fortran_compiler": item(bool(_version("gfortran")), "Fortran build profiles require a configured compiler", _version("gfortran")),
            "runtime_instrumentation": item(execution_profiles != "[]", "Requires a host-owned execution profile"),
            "build_recovery": item(build_profiles != "[]", "Requires a host-owned build profile"),
            "multi_agent_git": item(git_repo_ready, "Requires Git and a registered Git repository", git),
            "postgresql": item(postgres, "Set RINTEL_DATABASE_URL for the advanced PostgreSQL backend"),
            "browser_e2e": item(browser_ready, "Optional browser tests require @playwright/test and Chrome or Chromium", str(browser_root)),
        },
    }


def doctor(*, json_output: bool = False) -> None:
    report = capability_report()
    if json_output:
        print(json.dumps(report, indent=2))
        return
    for group in ("required", "optional"):
        print(f"{group} capabilities:")
        for name, value in report[group].items():
            print(f"  {name}: {value['status']} — {value['why']}")


def serve(*, port: int = 8000, open_browser: bool = True) -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12+ is required. Run ./install.sh with uv available.")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"127.0.0.1:{port} is unavailable: {exc}") from exc
    configure_local_paths()
    from .server.settings import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    if not (Path(settings.web_dist_path) / "index.html").is_file():
        raise RuntimeError("Local UI is missing. Run ./install.sh to build web/dist.")
    if not settings.database_url:
        from .db import Database
        db = Database(settings.sqlite_path)
        db.close()
    else:
        from .pg.pgstore import PgStore
        store = PgStore(settings.database_url, schema=settings.pg_schema)
        store.close()
    url = f"http://127.0.0.1:{port}"
    print(f"Rintel is available at {url}", flush=True)
    if open_browser:
        threading.Timer(2, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run("rintel.server.app:app", host="127.0.0.1", port=port)
