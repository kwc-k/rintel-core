"""TOPO-UI0 / SOFTWARE-DRC0 Software Topology endpoints.

Read-only: serve the frozen upstream topology/module artifacts as a
renderer-neutral view bundle (see rintel.topology_view.projector) and the
deterministic DRC run over them (rintel.drc).  No analysis, no mutation,
no truth upgrades.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from ...drc.run import lane_drc_input, run_drc
from ...topology_view.projector import build_lane_bundle, lane_list

router = APIRouter(prefix="/topology-view", tags=["topology-view"])


@router.get("/lanes")
def lanes() -> dict:
    return {"items": lane_list(), "total": len(lane_list())}


@router.get("/{lane}")
def bundle(lane: str) -> dict:
    try:
        return build_lane_bundle(lane)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "code": "lane_not_found",
            "message": f"no frozen topology artifacts for lane '{lane}'",
            "details": {"lane": lane},
        })


@lru_cache(maxsize=16)
def _drc_run(lane: str) -> dict:
    inp, dataflows, facts_nodes = lane_drc_input(lane)
    run = run_drc(lane, inp, dataflows=dataflows, facts_nodes=facts_nodes)
    return run.to_dict()


@router.get("/{lane}/drc")
def drc(lane: str) -> dict:
    try:
        return _drc_run(lane)
    except KeyError:
        raise HTTPException(status_code=404, detail={
            "code": "lane_not_found",
            "message": f"no frozen topology artifacts for lane '{lane}'",
            "details": {"lane": lane},
        })
