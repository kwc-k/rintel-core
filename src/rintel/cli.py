"""rintel CLI: index / verify / search / dump / stats / diff (spec §43)."""
from __future__ import annotations

import argparse
import json
import sys

from . import __release_tag__, __version__
from .db import SCHEMA_VERSION, Database
from .harness import append_jsonl, print_table, verify_fixture
from .indexer import Indexer
from .release import doctor, serve


def cmd_index(args):
    db = Database(args.db)
    idx = Indexer(db, args.path, repo_id=args.label, force=args.force,
                  commit=args.commit)
    res = idx.index()
    print(json.dumps({
        "run_id": res.run_id, "repo": res.repo_id, "snapshot": res.snapshot_id,
        "incremental": res.incremental, "files_total": res.files_total,
        "files_parsed": res.files_parsed, "files_unchanged": res.files_unchanged,
        "files_failed": res.files_failed, "duration_ms": res.duration_ms,
    }, indent=2))
    db.close()


def cmd_verify(args):
    metrics = []
    paths = args.paths
    for p in paths:
        if args.recursive:
            from pathlib import Path
            for gt in sorted(Path(p).glob("*/ground_truth.json")):
                metrics.append(verify_fixture(gt.parent, line_tolerance=args.tol))
        else:
            metrics.append(verify_fixture(p, line_tolerance=args.tol))
    print_table(metrics)
    out = args.out
    if out:
        for m in metrics:
            append_jsonl(m, out)
        print(f"\nraw results appended to {out}/verify-*.jsonl")


def _repo(db, args) -> str:
    if getattr(args, "repo", None):
        return args.repo
    rows = db.conn.execute("SELECT id FROM repos ORDER BY created_at LIMIT 1")
    row = rows.fetchone()
    if row:
        return row["id"]
    print("no repository in db; run `ri index` first", file=sys.stderr)
    sys.exit(1)


def cmd_search(args):
    db = Database(args.db)
    repo = _repo(db, args)
    sid = db.current_snapshot(repo)
    if sid is None:
        print("no snapshot; run `ri index` first", file=sys.stderr)
        sys.exit(1)
    for r in db.search(repo, args.query, limit=args.limit):
        print(f"{r['kind']:<10} {r['qname']:<50} {r['path']}:{r['start_line']}"
              f"  [{r['match']}]")
    db.close()


def cmd_dump(args):
    db = Database(args.db)
    repo = _repo(db, args)
    sid = args.snapshot or db.current_snapshot(repo)
    if sid is None:
        print("no snapshot", file=sys.stderr)
        sys.exit(1)
    exp = db.view_export(repo, sid, view=args.view, max_edges=args.max)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(exp, f, indent=2)
        print(f"exported {len(exp['nodes'])} nodes / {len(exp['edges'])} edges"
              f" to {args.out}")
    else:
        print(json.dumps({k: v for k, v in exp.items() if k != "nodes"},
                         indent=2))
        for n in exp["nodes"][:args.max]:
            print(f"  N {n['kind']:<10} {n['qname']}")
        for e in exp["edges"][:args.max]:
            print(f"  E {e['kind']:<10} {e['src_id'][:60]} -> {e['dst_id'][:60]}")
    db.close()


def cmd_stats(args):
    db = Database(args.db)
    repo = _repo(db, args)
    print(json.dumps(db.stats(repo), indent=2))
    db.close()


def cmd_diff(args):
    db = Database(args.db)
    repo = _repo(db, args)
    snaps = db.snapshots(repo)
    if len(snaps) < 2:
        print("need at least two snapshots", file=sys.stderr)
        sys.exit(1)
    s1 = args.from_snap or snaps[-2]["id"]
    s2 = args.to_snap or snaps[-1]["id"]
    d = db.snapshot_diff(repo, s1, s2)
    print(json.dumps({k: (v if k != "changed_files" else v)
                      for k, v in d.items()}, indent=2))
    db.close()


def cmd_doctor(args):
    doctor(json_output=args.json)


def cmd_serve(args):
    try:
        serve(port=args.port, open_browser=not args.no_browser)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Rintel could not start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ri",
                                 description="rintel — repository intelligence "
                                             "indexer (v" + __version__ + ")")
    ap.add_argument("--version", action="version",
                    version=f"rintel {__version__} (tag {__release_tag__}; schema {SCHEMA_VERSION})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="check required and optional capabilities")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("serve", help="start the local UI and API on loopback")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("index", help="repository -> evidence.db")
    p.add_argument("path")
    p.add_argument("--db", default="evidence.db")
    p.add_argument("--label", default=None, help="repo id (default: dir name)")
    p.add_argument("--force", action="store_true", help="full re-index")
    p.add_argument("--commit", default=None)
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("verify", help="run T0 fixture correctness harness")
    p.add_argument("paths", nargs="+")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="verify every */ground_truth.json under paths")
    p.add_argument("--tol", type=int, default=1, help="line tolerance")
    p.add_argument("--out", default=None, help="results dir for raw JSONL")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("search", help="symbol/path/text search")
    p.add_argument("db")
    p.add_argument("query")
    p.add_argument("--repo", default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("dump", help="export a projection (JSON)")
    p.add_argument("db")
    p.add_argument("--repo", default=None)
    p.add_argument("--snapshot", default=None)
    p.add_argument("--view", default="all", choices=["all", "architecture", "symbol"])
    p.add_argument("--max", type=int, default=1000)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_dump)

    p = sub.add_parser("stats", help="graph statistics")
    p.add_argument("db")
    p.add_argument("--repo", default=None)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("diff", help="snapshot diff (spec §40)")
    p.add_argument("db")
    p.add_argument("--repo", default=None)
    p.add_argument("--from", dest="from_snap", default=None)
    p.add_argument("--to", dest="to_snap", default=None)
    p.set_defaults(fn=cmd_diff)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
