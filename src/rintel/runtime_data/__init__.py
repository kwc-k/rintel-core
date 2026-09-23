"""RUNTIME-DATA0: decode selective data probes into RuntimeDataObject /
RuntimeLocation / DataAccessEvent / RuntimeBinding facts over the CRM solver
chain (bmatrix matrix/x/a/b/rex/ipiv regions, LBLOCK.nb state, DGESV A/B).

Probe format (scripts/runtime_data0/rtdata.c):
  header 18B: u8 magic(0x5B) u8 ver u64 t0 u64 reserved
  record 17B + payload: u8 kind(1 alloc/2 free/3 region/4 sci/5 scd)
      | u32 tag | u64 ts(ns rel t0) | u32 nbytes | payload

Static layout of the real bmatrix buffer (n = blocks->dim, doubles):
  SetBlocks (faclib/crm.c:1538): k = 2*k*(k+2); bmatrix = malloc(k*8)
  matrix  [0, n*n)        BlockMatrix fill/diagonalisation
  x       [n*n, n*n+n)    FixNorm normalisation vector
  a       [n*n+n, 2n*n+n) BlockPopulation packed A (m x n, lda=n)
  b       [2n*n+n, 2n*n+2n)  packed RHS / solution (m x 1, ldb=n)
  ipiv    [2n*n+2n, 2n*n+3n) int pivot index array (DGESV output)
  rex     [2n*n+3n, 2n*n+4n)  radiative/ionisation residual (BlockMatrix)
"""
import struct
from pathlib import Path

from .model import (RuntimeBinding, DataAccessEvent, RuntimeDataObject,
                    RuntimeLocation)

RTD_OUT = Path(__file__).resolve().parents[3] / "analysis_tournament" / "runtime_data0"

# tag -> (name, kind) -- MUST match scripts/runtime_data0/rtdata.h enum
TAGS = {
    1: ("SetBlocks.dim", "sci"),
    2: ("bmatrix.alloc", "alloc"),
    3: ("bmatrix.free", "free"),
    4: ("BlockMatrix.dim", "sci"),
    5: ("BlockMatrix.fill", "region"),
    6: ("BlockMatrix.diag", "region"),
    7: ("FixNorm.dim", "sci"),
    8: ("FixNorm.mode", "sci"),
    9: ("FixNorm.out", "region"),
    10: ("BlockPopulation.dim", "sci"),
    11: ("BlockPopulation.in", "region"),
    12: ("BlockPopulation.m", "sci"),
    13: ("BlockPopulation.a_pre", "region"),
    14: ("BlockPopulation.b_pre", "region"),
    15: ("BlockPopulation.a_post", "region"),
    16: ("BlockPopulation.b_post", "region"),
    17: ("BlockPopulation.info", "sci"),
    18: ("BlockPopulation.ipiv", "region"),
    19: ("BlockPopulation.nb", "region"),
}

KIND_NAME = {1: "ALLOCATE", 2: "FREE", 3: "REGION", 4: "SCI", 5: "SCD"}

# witness source anchors (canonical FAC lines)
SRC_LINES = {
    "bmatrix.alloc": "faclib/crm.c:1538",
    "bmatrix.free": "faclib/crm.c:469",
    "BlockMatrix.fill": "faclib/crm.c:3357",
    "BlockMatrix.diag": "faclib/crm.c:3365",
    "FixNorm.out": "faclib/crm.c:3242",
    "BlockPopulation.in": "faclib/crm.c:3667",
    "BlockPopulation.a_pre": "faclib/crm.c:3729",
    "BlockPopulation.b_pre": "faclib/crm.c:3729",
    "BlockPopulation.a_post": "faclib/crm.c:3737",
    "BlockPopulation.b_post": "faclib/crm.c:3737",
    "BlockPopulation.info": "faclib/crm.c:3738",
    "BlockPopulation.ipiv": "faclib/crm.c:3737",
    "BlockPopulation.nb": "faclib/crm.c:3819",
    "dgesv.call": "faclib/crm.c:3734",
    "dgesv.doc": "lapack/dgesv.f:41",
    "f2c.binding": "faclib/f2c.h:94",
}

HDR = struct.Struct("<BBQQ")   # 18 bytes
REC = struct.Struct("<BIQI")   # 17 bytes


def decode_rtdata(path):
    """-> (meta, records); records = [{kind,kind_name,tag,name,ts_ns,seq,payload...}]"""
    raw = Path(path).read_bytes()
    if len(raw) < 18:
        raise ValueError(f"rtdata file too short: {path}")
    magic, ver, t0, _ = HDR.unpack_from(raw, 0)
    if magic != 0x5B:
        raise ValueError(f"bad magic {magic:#x}")
    recs, off, seq = [], 18, 0
    while off + 17 <= len(raw):
        kind, tag, ts, n = REC.unpack_from(raw, off)
        off += 17
        payload = raw[off:off + n]
        off += n
        name = TAGS.get(tag, (f"tag{tag}", "unknown"))[0]
        r = {"kind": kind, "kind_name": KIND_NAME.get(kind, "?"), "tag": tag,
             "name": name, "ts_ns": ts, "seq": seq}
        seq += 1
        if kind == 1:
            r["ptr"], r["bytes"] = struct.unpack("<QQ", payload[:16])
        elif kind == 2:
            r["ptr"] = struct.unpack("<Q", payload[:8])[0]
        elif kind == 3:
            r["data"] = payload
            r["bytes"] = len(payload)
        elif kind == 4:
            r["value"] = struct.unpack("<q", payload[:8])[0]
        elif kind == 5:
            r["value"] = struct.unpack("<d", payload[:8])[0]
        recs.append(r)
    return {"magic": magic, "ver": ver, "t0": t0, "file": str(path),
            "records": len(recs)}, recs


def bmatrix_regions(n: int):
    """region name -> (offset_doubles, n_units) within bmatrix (ipiv = ints)."""
    return {
        "matrix": (0, n * n),
        "x": (n * n, n),
        "a": (n * n + n, n * n),
        "b": (2 * n * n + n, n),
        "ipiv": (2 * n * n + 2 * n, n),
        "rex": (2 * n * n + 3 * n, n),
    }


# probe tag -> [(region, operation_kind, unit_bytes, scope), ...]
TAG_EVENTS = {
    5: [("matrix", "WRITE", 8, "STATE"), ("rex", "WRITE", 8, "STATE")],
    6: [("matrix", "WRITE", 8, "STATE")],
    9: [("x", "WRITE", 8, "STATE"), ("matrix", "WRITE", 8, "STATE")],
    11: [("matrix", "READ", 8, "STATE"), ("x", "READ", 8, "STATE")],
    13: [("a", "WRITE", 8, "LOCAL")],
    14: [("b", "WRITE", 8, "LOCAL")],
    15: [("a", "READ_WRITE", 8, "LOCAL")],
    16: [("b", "READ_WRITE", 8, "LOCAL")],
    18: [("ipiv", "WRITE", 4, "LOCAL")],
    19: [("nb-state", "WRITE", 8, "STATE")],
}

REGION_DTYPE = {"matrix": "float64", "x": "float64", "a": "float64",
                "b": "float64", "rex": "float64", "ipiv": "int32",
                "nb-state": "float64"}

# region -> (rank, shape builder(dim, m))
REGION_SHAPE = {
    "matrix": (2, lambda dim, m: [dim, dim]),
    "x": (1, lambda dim, m: [dim]),
    "a": (2, lambda dim, m: [m, dim]),
    "b": (2, lambda dim, m: [m, 1]),
    "ipiv": (1, lambda dim, m: [m]),
    "rex": (1, lambda dim, m: [dim]),
    "nb-state": (1, lambda dim, m: [dim]),
}

REGION_ROLE = {
    "matrix": "rate matrix (n x n) inside shared static bmatrix",
    "x": "normalisation vector (FixNorm) inside bmatrix",
    "a": "packed DGESV A (m x n, lda=n) — BlockPopulation local cursor",
    "b": "packed DGESV B (m x 1, ldb=n) — BlockPopulation local cursor",
    "ipiv": "DGESV pivot indices (m) — BlockPopulation local cursor",
    "rex": "radiative/ionisation residual vector inside bmatrix",
    "nb-state": "LBLOCK.nb population results across blocks[] (STATE, not a port)",
}

REGION_CANON = {
    "matrix": "bmatrix",
    "x": "bmatrix",
    "rex": "bmatrix",
    "a": "a",
    "b": "b",
    "ipiv": "ipiv",
    "nb-state": "blocks",
}


def sym_for(name: str, sym_map: dict) -> str:
    if name in sym_map:
        return sym_map[name]
    return f"symbol:DATA:unresolved::{name}"


def _mk_edge(obj, e, last_writer):
    return {
        "kind": "OBSERVED_DATA_DEPENDENCE",
        "location_id": obj["canonical_location_id"],
        "writer_event_id": last_writer["event_id"],
        "writer_invocation_id": last_writer["invocation_id"],
        "reader_event_id": e["event_id"],
        "reader_invocation_id": e["invocation_id"],
        "observed_order": e["order"] > last_writer["order"],
    }


def _mk_lw(obj, e, last_writer):
    return {
        "location_id": obj["canonical_location_id"],
        "reader_event_id": e["event_id"],
        "observed_last_writer": last_writer["event_id"],
        "last_writer_invocation_id": last_writer["invocation_id"],
    }


def build_run_data(records, run_id: str, revision: str,
                   sym_map: dict, trace_rows: list, run_meta: dict) -> dict:
    """Assemble run-W*.data.json payload.  trace_rows = invocation rows of the
    same run (from runtime_trace decode); f_dgesv rows are paired positionally
    with pre-DGESV probe clusters."""
    dims = [r["value"] for r in records
            if r["name"] in ("SetBlocks.dim", "BlockMatrix.dim",
                             "FixNorm.dim", "BlockPopulation.dim")]
    n = dims[0] if dims else None
    if n is None:
        raise ValueError("no dimension probe observed")
    m_vals = [r["value"] for r in records if r["name"] == "BlockPopulation.m"]
    info_vals = [r["value"] for r in records if r["name"] == "BlockPopulation.info"]
    if not m_vals:
        raise ValueError("no m probe observed")

    dgesv_rows = sorted([r for r in trace_rows
                         if str(r.get("symbol_name", "")).endswith("f_dgesv")],
                        key=lambda r: r.get("start_ts", 0))
    n_calls = len(dgesv_rows)
    if n_calls != len(m_vals):
        dgesv_rows = dgesv_rows[:len(m_vals)]

    regions = bmatrix_regions(n)
    objects, locations = {}, {}
    alloc_ptr = alloc_bytes = None
    for r in records:
        if r["name"] == "bmatrix.alloc":
            alloc_ptr, alloc_bytes = str(r["ptr"]), r["bytes"]
    alloc_id = alloc_ptr or "static/unknown"

    for region, (off, cnt) in regions.items():
        unit = 4 if region == "ipiv" else 8
        extent = cnt * unit
        row_cnt = (m_vals[0] if m_vals else n) if region in ("a", "b", "ipiv") else n
        if region == "ipiv":
            row_cnt = m_vals[0] if m_vals else n
        rank, shape_fn = REGION_SHAPE[region]
        shape = shape_fn(n, row_cnt)
        rid = f"rtd:{run_id}:{region}"
        loc_id = f"rtloc:{run_id}:bmatrix:{region}:v1"
        locations[loc_id] = {
            "location_id": loc_id, "run_id": run_id,
            "canonical_symbol_id": sym_for(REGION_CANON[region], sym_map),
            "region": region,
            "offset_bytes": off * 8, "extent_bytes": extent,
            "allocation_identity": alloc_id,
            "version": 1, "created_event_id": None, "freed_event_id": None,
            "static_description": REGION_ROLE[region],
            "note": (f"region [{off},{off + cnt}) of bmatrix (n={n}); "
                     f"allocation identity {alloc_id}"),
        }
        objects[region] = {
            "runtime_data_id": rid, "run_id": run_id,
            "canonical_symbol_id": sym_for(REGION_CANON[region], sym_map),
            "canonical_location_id": loc_id,
            "dtype": REGION_DTYPE[region], "rank": rank, "shape": shape,
            "shape_status": "RESOLVED",
            "element_count": shape[0] * (shape[1] if len(shape) > 1 else 1),
            "byte_size": extent, "logical_size": extent,
            "allocation_identity": alloc_id,
            "truth_class": "OBSERVED",
            "coverage": "PARTIAL" if region == "nb-state" else "COMPLETE",
            "scope": {"matrix": "STATE", "x": "STATE", "rex": "STATE",
                      "a": "LOCAL", "b": "LOCAL", "ipiv": "LOCAL",
                      "nb-state": "STATE"}[region],
            "witness": {"dim_n": n, "m": m_vals[0] if m_vals else None,
                        "probe_records": [r["name"] for r in records
                                          if r["kind"] == 3
                                          and TAGS.get(r["tag"], ("",))[0]
                                          in SRC_LINES]},
            "producer_event_ids": [], "consumer_event_ids": [],
        }
    # nb-state object: LBLOCK.nb across blocks[] (NOT inside the bmatrix buffer)
    nb_loc = f"rtloc:{run_id}:state:blocks.nb:v1"
    locations[nb_loc] = {
        "location_id": nb_loc, "run_id": run_id,
        "canonical_symbol_id": sym_for("blocks", sym_map),
        "region": "nb-state",
        "offset_bytes": 0, "extent_bytes": n * 8,
        "allocation_identity": "blocks ARRAY (ArrayGet per element)",
        "version": 1, "created_event_id": None, "freed_event_id": None,
        "static_description": "LBLOCK.nb (double) of every block, reached via "
                              "ArrayGet(blocks,i) — a STATE field, not a Port",
        "note": f"per-element field, elements blocks[0..{n})",
    }
    rank_nb, shape_fn_nb = REGION_SHAPE["nb-state"]
    nb_shape = shape_fn_nb(n, n)
    objects["nb-state"] = {
        "runtime_data_id": f"rtd:{run_id}:nb-state", "run_id": run_id,
        "canonical_symbol_id": sym_for("blocks", sym_map),
        "canonical_location_id": nb_loc,
        "dtype": "float64", "rank": rank_nb, "shape": nb_shape,
        "shape_status": "RESOLVED",
        "element_count": n, "byte_size": n * 8, "logical_size": n * 8,
        "allocation_identity": "blocks ARRAY",
        "truth_class": "OBSERVED", "coverage": "PARTIAL",
        "scope": "STATE",
        "witness": {"dim_n": n, "note": "only nb field captured (n[]/r[]/total_rate "
                                        "writes not probed)"},
        "producer_event_ids": [], "consumer_event_ids": [],
    }
    # info scalar object
    objects["info"] = {
        "runtime_data_id": f"rtd:{run_id}:info", "run_id": run_id,
        "canonical_symbol_id": sym_for("info", sym_map),
        "canonical_location_id": f"rtloc:{run_id}:bmatrix:info:v1",
        "dtype": "int32", "rank": 0, "shape": [], "shape_status": "RESOLVED",
        "element_count": 1, "byte_size": 4, "logical_size": 4,
        "allocation_identity": "stack-local &info", "truth_class": "OBSERVED",
        "coverage": "COMPLETE", "scope": "LOCAL",
        "witness": {"dim_n": n}, "producer_event_ids": [], "consumer_event_ids": [],
    }

    # --- events ---
    events, order = [], 0
    cur_inv = "rt:probe"
    cur_call = -1

    def inv_of(i):
        return (dgesv_rows[i]["invocation_id"] if i < len(dgesv_rows) else "rt:unknown")

    def emit(region, op_kind, rec, note, inv_id):
        nonlocal order
        events.append({
            "event_id": f"{run_id}:e{order:04d}", "run_id": run_id,
            "invocation_id": inv_id, "operation_kind": op_kind,
            "runtime_data_id": objects[region]["runtime_data_id"],
            "canonical_location_id": objects[region]["canonical_location_id"],
            "ts_ns": rec["ts_ns"], "order": order,
            "source_line": SRC_LINES.get(rec["name"]) if rec["name"] in SRC_LINES
            else None,
            "byte_extent": objects[region]["byte_size"],
            "note": note})
        k = "producer_event_ids" if op_kind in ("WRITE", "ALLOCATE") else "consumer_event_ids"
        objects[region][k].append(events[-1]["event_id"])
        order += 1

    for rec in records:
        name, kind = TAGS.get(rec["tag"], ("", ""))
        if rec["kind"] == 1 and name == "bmatrix.alloc":
            emit("matrix", "ALLOCATE", rec,
                 f"SetBlocks alloc {rec['bytes']} bytes = 2n(n+2) doubles", "rt:alloc:SetBlocks")
        elif rec["kind"] == 2 and name == "bmatrix.free":
            emit("matrix", "FREE", rec,
                 "ReinitCRM free (RateCoefficients path)", "rt:free:ReinitCRM")
        elif rec["kind"] == 3 and rec["tag"] in TAG_EVENTS:
            if name == "BlockPopulation.a_pre":
                cur_call += 1
                cur_inv = inv_of(cur_call)
            elif name in ("BlockPopulation.in", "BlockPopulation.a_post",
                          "BlockPopulation.b_post", "BlockPopulation.ipiv"):
                cur_inv = inv_of(cur_call if cur_call >= 0 else 0)
            for region, op_kind, unit, scope in TAG_EVENTS[rec["tag"]]:
                emit(region, op_kind, rec, f"probe {name} ({rec['bytes']} bytes)",
                     cur_inv)
        elif rec["kind"] == 4 and name == "BlockPopulation.info":
            emit("info", "WRITE", rec, f"DGESV INFO = {rec['value']}",
                 inv_of(cur_call if cur_call >= 0 else 0))

    # --- lineage (OBSERVED_DATA_DEPENDENCE) ---
    lineage = {}
    for region, obj in objects.items():
        if region == "info":
            continue
        evs = sorted([e for e in events
                      if e["runtime_data_id"] == obj["runtime_data_id"]],
                     key=lambda e: e["order"])
        edges, lws, last_writer = [], [], None
        for e in evs:
            if e["operation_kind"] in ("WRITE", "READ_WRITE", "ALLOCATE"):
                if e["operation_kind"] == "READ_WRITE" and last_writer is not None:
                    edges.append(_mk_edge(obj, e, last_writer))
                    lws.append(_mk_lw(obj, e, last_writer))
                last_writer = e
            elif e["operation_kind"] == "READ" and last_writer is not None:
                edges.append(_mk_edge(obj, e, last_writer))
                lws.append(_mk_lw(obj, e, last_writer))
        lineage[region] = {"edges": edges, "last_writer": lws}

    # --- value samples from probe payloads (RD4-style evidence) ---
    samples = {}
    idx = {}
    for rec in records:
        if rec["kind"] != 3 or "data" not in rec:
            continue
        name = TAGS.get(rec["tag"], ("", ""))[0]
        if name not in ("BlockPopulation.nb", "BlockPopulation.a_post",
                        "BlockPopulation.a_pre", "BlockPopulation.b_post",
                        "BlockPopulation.ipiv", "BlockMatrix.fill"):
            continue
        key = {"BlockPopulation.nb": "nb_post",
               "BlockPopulation.a_post": "a_post",
               "BlockPopulation.a_pre": "a_pre",
               "BlockPopulation.b_post": "b_post",
               "BlockPopulation.ipiv": "ipiv",
               "BlockMatrix.fill": "matrix_fill"}[name]
        k = idx.get(key, 0)
        idx[key] = k + 1
        blob = rec["data"]
        if name == "BlockPopulation.ipiv":
            vals = struct.unpack(f"<{len(blob) // 4}i", blob[: (len(blob) // 4) * 4])
            sample = {"first8": list(vals[:8]), "min": min(vals), "max": max(vals),
                      "pivoting_observed": any(v != i + 1 for i, v in enumerate(vals[: min(len(vals), 8)]))}
        else:
            vals = struct.unpack(f"<{len(blob) // 8}d", blob[: (len(blob) // 8) * 8])
            sample = {"first8": [round(v, 6) for v in vals[:8]],
                      "min": round(min(vals), 6), "max": round(max(vals), 6),
                      "nonzero": sum(1 for v in vals if v != 0.0),
                      "all_zero": all(v == 0.0 for v in vals)}
        samples.setdefault(key, []).append(sample)

    return {
        "run_id": run_id, "revision": revision,
        "value_samples": samples,
        "observed_dim": n, "observed_m": m_vals, "info_values": info_vals,
        "records": len(records),
        "objects": objects, "locations": locations, "events": events,
        "lineage": lineage,
        "dgesv_call_count_in_trace": n_calls,
        "run_meta": run_meta,
    }


def dgesv_bindings(run_data: dict, run_id: str, sym_map: dict) -> list:
    """RuntimeBinding list: caller runtime objects -> dgesv_ / f_dgesv formals.
    DGESV(m, nrhs, a, lda, ipiv, b, ldb, &info) — positional per f2c.h:94."""
    objects = run_data["objects"]
    n, m = run_data["observed_dim"], run_data["observed_m"]
    formals = ["N", "NRHS", "A", "LDA", "IPIV", "B", "LDB", "INFO"]
    binds = []
    for i, m_i in enumerate(m):
        actual = {
            "N": {"val": m_i, "obj": None},
            "NRHS": {"val": 1, "obj": None},
            "A": {"val": None, "obj": objects["a"]["runtime_data_id"]},
            "LDA": {"val": n, "obj": None},
            "IPIV": {"val": None, "obj": objects["ipiv"]["runtime_data_id"]},
            "B": {"val": None, "obj": objects["b"]["runtime_data_id"]},
            "LDB": {"val": n, "obj": None},
            "INFO": {"val": (run_data.get("info_values") or [None])[i] if len(
                run_data.get("info_values") or []) > i else None,
                "obj": objects["info"]["runtime_data_id"]},
        }
        for pos, formal in enumerate(formals):
            binds.append({
                "binding_id": f"rtb:{run_id}:dgesv{i}:{formal}",
                "run_id": run_id,
                "call_invocation_id": "dgesv-call-%d" % (i + 1),
                "caller_object_id": actual[formal]["obj"],
                "actual_value": actual[formal]["val"],
                "formal_symbol_id": sym_for(formal, sym_map),
                "callee_symbol_id": sym_for("dgesv_", sym_map),
                "position": pos,
                "truth": "EXACT",
                "evidence": {
                    "source": "faclib/crm.c:3734 (call site)",
                    "mapping": "faclib/f2c.h:94 (DGESV -> f_dgesv positional)",
                    "lapack_semantics": "lapack/dgesv.f:41 A(input/output), :53 B(input/output), "
                                  ":49-51 IPIV(output), :60-63 INFO(output)",
                },
            })
    return binds
