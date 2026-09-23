"""JoernProvider — Joern sidecar (fixed operations) → AnalysisFact.

Frozen constraints (ANALYZER-MAP0 / FLOW1-ANALYZER0 §7):
- The adapter only ever issues FIXED operations (symbols / calls / cfg /
  dataflow / slice) through the shipped `joern_ops.sc` script.  Arbitrary
  CPGQL is NOT possible through this interface (browser/API never sends
  queries).
- `joern --script` invocations run with JAVA_HOME=JDK17 and the launcher
  cwd inside joern-cli (the macOS wrapper needs ./bin/repl-bridge and a
  greadlink shim on PATH — see analysis-tools/bin).
- Joern's reachableByFlows output is conservative MAY-flow:
  every DATA_FLOW fact derived from it is mapped to
  truth_class=INFERRED — NEVER OBSERVED (frozen rule).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from .contract import (AnalysisFact, Coverage, ExecutionModality, FactKind,
                       ProviderCapabilities, SemanticKind, SourceLocation,
                       TargetResolution, TruthClass)
from .provider import AnalysisProvider, Scope

_JDK17 = "/Library/Java/JavaVirtualMachines/liberica-jdk-17.jdk/Contents/Home"
_JOERN_CLI = "/Users/wu/Documents/dh/a3/fac/analysis-tools/joern/joern-cli"
_TOOLS_BIN = "/Users/wu/Documents/dh/a3/fac/analysis-tools/bin"
_SCRIPT = str(Path(__file__).parent / "scripts" / "joern_ops.sc")


class JoernProvider(AnalysisProvider):
    provider_id = "joern"

    def __init__(self, joern_cli: str = _JOERN_CLI, script: str = _SCRIPT,
                 jdk_home: str = _JDK17, tools_bin: str = _TOOLS_BIN,
                 repo_id: str = "", snapshot_id: Optional[str] = None,
                 language: str = "python", timeout: float = 600.0):
        self._cli = joern_cli
        self._script = script
        self._jdk = jdk_home
        self._tools_bin = tools_bin
        self._repo_id = repo_id
        self._snapshot_id = snapshot_id
        self._language = language
        self._timeout = timeout
        self._cache: dict[tuple, dict[str, Any]] = {}
        self.provider_version = self._detect_version() or "?"

    def _detect_version(self) -> Optional[str]:
        env = dict(os.environ)
        env["JAVA_HOME"] = self._jdk
        env["PATH"] = f"{self._tools_bin}:{env.get('PATH', '')}"
        proc = subprocess.run([str(Path(self._cli) / "joern"), "--version"],
                              capture_output=True, text=True, timeout=60,
                              cwd=self._cli, env=env)
        text = (proc.stdout or "") + (proc.stderr or "")
        for line in text.splitlines():
            if "Version:" in line:
                return line.split("Version:", 1)[1].strip()
        return None

    def run_op(self, op: str, input_path: str, symbol: str = "",
               language: Optional[str] = None, force: bool = False) -> dict:
        """Run one fixed Joern operation; returns the raw JSON dict.

        The whole repo is re-imported per invocation (sidecar semantics);
        results are cached per (input, op, symbol, language).
        """
        lang = language or self._language
        key = (input_path, op, symbol, lang)
        if not force and key in self._cache:
            return self._cache[key]
        env = dict(os.environ)
        env["JAVA_HOME"] = self._jdk
        env["PATH"] = f"{self._tools_bin}:{env.get('PATH', '')}"
        cmd = [str(Path(self._cli) / "joern"), "--script", self._script,
               "--param", f"input={input_path}",
               "--param", f"op={op}",
               "--param", f"symbol={symbol}",
               "--param", f"language={lang}"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self._timeout, cwd=self._cli, env=env)
        if proc.returncode != 0:
            raise RuntimeError(
                f"joern op '{op}' failed rc={proc.returncode}: "
                f"{proc.stderr[-500:]}")
        # the script prints exactly one ujson line on stdout
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                self._cache[key] = data
                return data
        raise RuntimeError(f"joern op '{op}' produced no JSON: "
                           f"{proc.stdout[-300:]!r}")

    # -- capabilities ---------------------------------------------------------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(symbols=True, calls=True, cfg=True,
                                    dataflow=True, signatures=False,
                                    types=False, diagnostics=False,
                                    cross_language=True)

    # -- fact mapping ---------------------------------------------------------
    def _run(self, scope: Scope, op: str, symbol: str = "") -> dict:
        path = scope.file or scope.extra.get("repo_path")
        if not path:
            raise RuntimeError("JoernProvider needs scope.file (repo path)")
        return self.run_op(op, path, symbol=symbol or scope.symbol or "",
                           language=scope.language or self._language)

    @staticmethod
    def _loc(item: dict, path: str) -> Optional[SourceLocation]:
        ln = item.get("lineNumber") or item.get("line")
        if isinstance(ln, list):
            ln = ln[0] if ln else None
        if ln is None or not isinstance(ln, (int, float)):
            return None
        fn = item.get("filename")
        return SourceLocation(file=str(fn or path), line=int(ln))

    def symbols(self, scope: Scope):
        self._require("symbols")
        data = self._run(scope, "symbols")
        for it in data.get("items", []):
            if "<" in str(it.get("name") or ""):
                continue
            yield AnalysisFact(
                fact_id=f"joern:sym:{it.get('fullName')}",
                fact_kind=FactKind.SYMBOL, semantic_kind=SemanticKind.DECLARATION,
                provider=self.provider_id, provider_version=self.provider_version,
                repo_id=self._repo_id, snapshot_id=self._snapshot_id,
                truth_class=TruthClass.OBSERVED,
                execution_modality=ExecutionModality.MUST,
                target_resolution=TargetResolution.EXACT,
                coverage=Coverage.COMPLETE,
                subject=it.get("fullName") or it.get("name"),
                source_location=self._loc(it, str(scope.file or "")),
                metadata={"name": it.get("name"), "filename": it.get("filename")})

    def calls(self, scope: Scope):
        self._require("calls")
        data = self._run(scope, "calls")
        for it in data.get("items", []):
            if "<" in str(it.get("name") or "") or not it.get("caller"):
                continue
            mfn = str(it.get("methodFullName") or "")
            resolved = mfn != "<unknownFullName>" and bool(mfn)
            yield AnalysisFact(
                fact_id=f"joern:call:{it.get('caller')}:{it.get('name')}@{it.get('lineNumber')}",
                fact_kind=FactKind.CALL, semantic_kind=SemanticKind.CALL,
                provider=self.provider_id, provider_version=self.provider_version,
                repo_id=self._repo_id, snapshot_id=self._snapshot_id,
                truth_class=TruthClass.OBSERVED,
                execution_modality=ExecutionModality.MUST,
                target_resolution=(TargetResolution.EXACT if resolved
                                   else TargetResolution.UNKNOWN),
                coverage=(Coverage.COMPLETE if resolved else Coverage.UNKNOWN),
                subject=str(it.get("caller") or ""),
                target=mfn if resolved else str(it.get("name") or ""),
                source_location=self._loc(it, str(scope.file or "")),
                metadata={"call_name": it.get("name"),
                          "resolved": resolved})

    def control_flow(self, scope: Scope):
        self._require("cfg")
        data = self._run(scope, "cfg", symbol=scope.symbol or "")
        for cs in data.get("controls", []):
            ln = cs.get("line")
            method = cs.get("method") or scope.symbol or ""
            sem = {"if": SemanticKind.BRANCH, "when": SemanticKind.BRANCH,
                   "switch": SemanticKind.BRANCH,
                   "for": SemanticKind.LOOP, "while": SemanticKind.LOOP,
                   "do": SemanticKind.LOOP}.get(
                       str(cs.get("name") or "").lower(), SemanticKind.BRANCH)
            yield AnalysisFact(
                fact_id=f"joern:ctrl:{cs.get('method')}:{cs.get('line')}",
                fact_kind=FactKind.CONTROL_FLOW, semantic_kind=sem,
                provider=self.provider_id, provider_version=self.provider_version,
                repo_id=self._repo_id, snapshot_id=self._snapshot_id,
                truth_class=TruthClass.OBSERVED,
                execution_modality=ExecutionModality.MUST,
                target_resolution=TargetResolution.EXACT,
                coverage=Coverage.COMPLETE,
                subject=str(method or ""),
                guard=str(cs.get("code") or ""),
                source_location=SourceLocation(
                    file=str(scope.file or ""), line=int(ln)) if ln else None,
                metadata={"ctrl_name": cs.get("name")})

    def data_flow(self, scope: Scope):
        """Joern may-flows → INFERRED (frozen rule — never OBSERVED)."""
        self._require("dataflow")
        data = self._run(scope, "dataflow", symbol=scope.symbol or "")
        for fi, flow in enumerate(data.get("items", [])):
            elems = flow.get("elements", [])
            for a, b in zip(elems, elems[1:]):
                yield AnalysisFact(
                    fact_id=f"joern:df:{fi}:{a.get('line')}->{b.get('line')}",
                    fact_kind=FactKind.DATA_FLOW,
                    semantic_kind=SemanticKind.READ,
                    provider=self.provider_id,
                    provider_version=self.provider_version,
                    repo_id=self._repo_id, snapshot_id=self._snapshot_id,
                    truth_class=TruthClass.INFERRED,
                    execution_modality=ExecutionModality.MAY,
                    target_resolution=TargetResolution.EXACT,
                    coverage=Coverage.PARTIAL,
                    subject=str(flow.get("sinkMethod") or ""),
                    source_location=SourceLocation(  # approx; metadata carries exact
                        file=str(scope.file or ""),
                        line=int(a.get("line")) if a.get("line") and int(a.get("line")) > 0 else None),
                    metadata={"from_code": a.get("code"), "to_code": b.get("code"),
                              "from_node": a.get("node"), "to_node": b.get("node"),
                              "from_line": a.get("line"), "to_line": b.get("line"),
                              "may_flow": True})

    # -- declared-but-not-implemented ----------------------------------------
    def signature(self, scope: Scope):
        self._require("signatures")
        return iter([])

    def type_of(self, scope: Scope):
        self._require("types")
        return iter([])

    def diagnostics(self, scope: Scope):
        self._require("diagnostics")
        return iter([])

    def source_location(self, entity: str) -> Optional[SourceLocation]:
        return None
