"""PostgreSQL parity for implicit EDA port ordinals."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from rintel.indexer import Indexer
from rintel.pg.pgschema import create_all
from rintel.pg.pgstore import PgStore


@pytest.fixture
def pg_store():
    dsn = os.environ.get("RINTEL_TEST_PG_DSN")
    if not dsn:
        pytest.skip("RINTEL_TEST_PG_DSN not set; PostgreSQL parity NOT_RUN")
    import psycopg

    schema = "core_loop0_port_" + uuid.uuid4().hex[:12]
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    admin.execute(f'CREATE SCHEMA "{schema}"')
    store = None
    try:
        admin.execute(f'SET search_path TO "{schema}", public')
        create_all(admin)
        store = PgStore(dsn, schema=schema)
        yield store
    finally:
        if store is not None:
            store.close()
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_pg_implicit_port_order_appends_per_direction(
        pg_store: PgStore, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "sample.py").write_text("def witness():\n    return 1\n")
    snapshot = Indexer(pg_store, root, repo_id="port-order-pg").index().snapshot_id
    flow = pg_store.flow_create_model("port-order-pg", "Ordered", snapshot)
    block = pg_store.flow_create_block(flow["id"], kind="function", name="Solver")
    ports = [pg_store.flow_create_port(
        flow["id"], block_id=block["id"], name=name,
        direction=direction, semantic_kind="data")
        for name, direction in (("A", "input"), ("rhs", "input"),
                                ("x", "output"), ("status", "output"))]
    assert [port["position_order"] for port in ports] == [0, 1, 0, 1]
    reread = pg_store.flow_ports(flow["id"])
    assert [(port["id"], port["position_order"]) for port in reread
            if port["direction"] == "input"] == [
                (ports[0]["id"], 0), (ports[1]["id"], 1)]
    assert [(port["id"], port["position_order"]) for port in reread
            if port["direction"] == "output"] == [
                (ports[2]["id"], 0), (ports[3]["id"], 1)]
