"""SYNTHESIS0 fixtures (spec §19 Y1–Y10) + production fixtures builder.

`YPIPE` is a small deterministic Python project; every scenario defines
AS-IS and TO-BE netlist sides plus the expected synthesis status.  The
closed loops run on real copies with real Indexer reindexes (Y6/Y8).
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_PATH = Path(__file__).resolve().parents[2] / \
    "analysis_tournament" / "synthesis" / "fixtures.json"

YPIPE_FILES = {
    "src/ypipe/__init__.py": "",
    "src/ypipe/run.py": (
        "from ypipe.store import save\n"
        "\n"
        "def run(data: dict, config: dict) -> dict:\n"
        "    result = transform(data)\n"
        "    save(result)\n"
        "    notify(result)\n"
        "    return {\"result\": result}\n"
        "\n"
        "def transform(data: dict) -> dict:\n"
        "    return {\"value\": data.get(\"v\", 0)}\n"
        "\n"
        "def notify(payload: dict) -> None:\n"
        "    return None\n"
    ),
    "src/ypipe/store.py": (
        "def save(record: dict) -> dict:\n"
        "    return {\"saved\": record}\n"
    ),
}

B = {
    "run": "node:FUNCTION:ypipe.run.run",
    "transform": "node:FUNCTION:ypipe.run.transform",
    "notify": "node:FUNCTION:ypipe.run.notify",
    "save": "node:FUNCTION:ypipe.store.save",
}


def block(bid: str, name: str, binding: str | None, state: str = "existing",
          resources: list[str] | None = None) -> dict:
    return {"id": bid, "name": name, "kind": "function", "state": state,
            "binding": binding, "resources": resources or []}


def port(pid: str, bid: str, name: str, direction: str,
         code_type: str | None = None) -> dict:
    return {"id": pid, "block_id": bid, "name": name,
            "direction": direction, "semantic_kind": "data",
            "code_type": code_type}


def net(nid: str, kind: str, src: str, tgt: str, label: str | None = None) -> dict:
    return {"id": nid, "kind": kind, "source_block_id": src,
            "target_block_id": tgt, "label": label}


def base_as_is() -> dict:
    return {
        "snapshot_id": "s-asis",
        "blocks": [
            block("b-run", "run", B["run"]),
            block("b-transform", "transform", B["transform"]),
            block("b-notify", "notify", B["notify"]),
            block("b-save", "save", B["save"]),
        ],
        "composites": [],
        "ports": [
            port("p1", "b-run", "data", "input", "dict"),
            port("p2", "b-run", "config", "input", "dict"),
            port("p3", "b-run", "result", "output", "dict"),
            port("p4", "b-transform", "data", "input", "dict"),
            port("p5", "b-transform", "result", "output", "dict"),
            port("p6", "b-notify", "payload", "input", "dict"),
            port("p7", "b-save", "record", "input", "dict"),
        ],
        "nets": [
            net("n1", "control", "b-run", "b-transform"),
            net("n2", "control", "b-run", "b-notify"),
            net("n3", "control", "b-run", "b-save"),
            net("n4", "data", "b-run", "b-transform", "data"),
            net("n5", "data", "b-transform", "b-run", "result"),
        ],
        "claims": [],
    }


def base_to_be() -> dict:
    return json.loads(json.dumps(base_as_is()))


# ---------------------------------------------------------------------------
# Y1 Add helper (A → Helper → B), multi-file
# ---------------------------------------------------------------------------

def y1() -> dict:
    to = base_to_be()
    to["blocks"].append(block("b-helper", "helper", None, state="proposed"))
    to["ports"].append(port("ph1", "b-helper", "data", "input", "dict"))
    to["ports"].append(port("ph2", "b-helper", "result", "output", "dict"))
    to["nets"].append(net("nh1", "control", "b-run", "b-helper"))
    to["nets"].append(net("nh2", "control", "b-helper", "b-transform"))
    return {"id": "Y1-add-helper", "label": "Add helper (multi-file)",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED",
            "files_touched_min": 2, "creates": 1}


# ---------------------------------------------------------------------------
# Y2 Add Cache with real DATA wiring
# ---------------------------------------------------------------------------

def y2() -> dict:
    to = base_to_be()
    to["blocks"].append(block("b-cache", "cache", None, state="proposed"))
    to["ports"].append(port("pc1", "b-cache", "data", "input", "dict"))
    to["ports"].append(port("pc2", "b-cache", "result", "output", "dict"))
    to["nets"].append(net("nc1", "control", "b-run", "b-cache"))
    to["nets"].append(net("nc2", "control", "b-cache", "b-transform"))
    to["nets"].append(net("nc3", "data", "b-run", "b-cache", "data"))
    return {"id": "Y2-add-cache", "label": "Add Cache w/ data wiring",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED", "files_touched_min": 2,
            "creates": 1}


# ---------------------------------------------------------------------------
# Y3 Signature expansion + known callers updated
# ---------------------------------------------------------------------------

def y3() -> dict:
    to = base_to_be()
    to["ports"].append(port("p4b", "b-transform", "config", "input", "dict"))
    return {"id": "Y3-signature-expansion",
            "label": "solve(data) → solve(data, config)",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED", "files_touched_min": 1,
            "expect_callers_updated": ["ypipe.run"]}


# ---------------------------------------------------------------------------
# Y4 Remove CALL
# ---------------------------------------------------------------------------

def y4() -> dict:
    to = base_to_be()
    to["nets"] = [n for n in to["nets"] if not (
        n["kind"] == "control" and n["target_block_id"] == "b-notify")]
    return {"id": "Y4-remove-call", "label": "Remove real call",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED"}


# ---------------------------------------------------------------------------
# Y5 Resource injection
# ---------------------------------------------------------------------------

def y5() -> dict:
    to = base_to_be()
    for b in to["blocks"]:
        if b["id"] == "b-save":
            b["resources"] = ["CacheResource"]
    return {"id": "Y5-resource-injection",
            "label": "Inject CacheResource dependency",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "PARTIAL",
            "note": "parameter injection is real; the resource EDGE cannot "
                    "be proven by the analyzer without a resource-detection "
                    "upgrade — honest PARTIAL per spec §22"}


# ---------------------------------------------------------------------------
# Y6 Composite boundary
# ---------------------------------------------------------------------------

def y6() -> dict:
    to = base_to_be()
    to["composites"].append({
        "id": "c-core", "name": "Core",
        "block_ids": ["b-run", "b-transform", "b-notify", "b-save"],
        "data_in": ["input"], "data_out": ["result"],
        "resources": [], "encapsulation": True})
    return {"id": "Y6-composite-boundary",
            "label": "Composite new external input",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED"}


# ---------------------------------------------------------------------------
# Y7 Partial impact — caller coverage PARTIAL must be preserved
# ---------------------------------------------------------------------------

def y7() -> dict:
    to = y3()["to_be"]
    return {"id": "Y7-partial-impact",
            "label": "Signature change with PARTIAL caller coverage",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "VERIFIED",
            "call_capability": "PARTIAL",
            "expect_partial_note": True}


# ---------------------------------------------------------------------------
# Y8 Illegal design (TO-BE has DRC ERROR) → BLOCKED
# ---------------------------------------------------------------------------

def y8() -> dict:
    to = base_to_be()
    to["composites"].append({
        "id": "c-core", "name": "Core",
        "block_ids": ["b-run", "b-transform", "b-notify", "b-save"],
        "data_in": [], "data_out": [], "resources": [],
        "encapsulation": True})
    to["nets"].append(net("nr1", "resource", "b-run", "CacheResource"))
    return {"id": "Y8-illegal-design",
            "label": "TO-BE hides a resource (DRC ERROR)",
            "as_is": base_as_is(), "to_be": to,
            "expect_status": "BLOCKED"}


# ---------------------------------------------------------------------------
# Y9 Generated patch breaks code (before_hash conflict) → FAILED
# ---------------------------------------------------------------------------

def y9() -> dict:
    return {"id": "Y9-patch-breaks",
            "label": "Apply conflict → FAILED (rollback recorded)",
            "as_is": base_as_is(), "to_be": y1()["to_be"],
            "expect_status": "FAILED",
            "conflict_on": "src/ypipe/run.py"}


# ---------------------------------------------------------------------------
# Y10 No-op
# ---------------------------------------------------------------------------

def y10() -> dict:
    return {"id": "Y10-noop", "label": "AS-IS == TO-BE",
            "as_is": base_as_is(), "to_be": base_to_be(),
            "expect_status": "NO_CHANGES"}


ALL = [y1(), y2(), y3(), y4(), y5(), y6(), y7(), y8(), y9(), y10()]


def build_fixtures_doc() -> dict:
    return {
        "generator": "rintel.synthesis.fixtures (SYNTHESIS0 §19)",
        "project": YPIPE_FILES,
        "fixtures": ALL,
    }


def write_fixtures(path: Path = FIXTURES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_fixtures_doc(),
                               ensure_ascii=False, indent=1))
    return path


if __name__ == "__main__":
    p = write_fixtures()
    print(f"synthesis fixtures written: {p} ({len(ALL)} scenarios)")
