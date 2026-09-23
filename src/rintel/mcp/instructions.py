"""RINTEL-MCP0 server instructions (§16) — shown to the Agent verbatim.

This file is the CANONICAL truth-contract text for every Rintel MCP client
(RINTEL-DSH0 §2).  Harnesses that mount this server must not maintain a
second, hand-written copy: the DSH preset mirrors this text verbatim through
`scripts/rintel_dsh0_sync_instructions.py`, and the trajectory audit compares
the mirror's sha256 with the text returned by `initialize` here.

No task-specific prompts; nothing that turns the audit into a tutorial.
"""
INSTRUCTIONS = """Rintel MCP — Code Intelligence Workbench.

RINTEL-DSH0 RULES (R1-R3, non-negotiable; cite them when you rely on them):
- R1: Adjacency, dependency or shared data does NOT imply authoritative change
  impact. Rintel has NO frozen change-impact capability. Never turn callers /
  callees / data relations / flow membership / runtime observations into a
  change-impact list. Say what the evidence is and stop there.
- R2: Every runtime or performance claim must carry its context: run id,
  workload, evidence revision, and instrumentation (e.g. W1 / run-w1-v1 /
  revision / -finstrument-functions). A claim without that context is not
  reportable. Instrumented timings are not absolute costs.
- R3: UNKNOWN / PARTIAL must never be promoted to FALSE / COMPLETE / certainty
  without additional evidence. "Not observed" is not "did not happen";
  "not modelled" is not "does not exist".

Truth rules (carry these into every answer):
- Evidence authority is assigned by Rintel's versioned lane registry, never by
  a producer claim. REFERENCE_EVIDENCE and DESIGN_ANNOTATION cannot enter
  canonical reconciliation or promote truth; corroboration adds provenance only.
- Query answer status (KNOWN_CANONICALLY / SUPPORTED_BY_REFERENCE /
  SUGGESTED_INFERRED / UNKNOWN) never replaces truth_class, resolution,
  coverage, execution_modality, or evidence_authority.
- Cross-language ABI bridges are explicit audited relationships, not canonical
  identity merges. Same spelling/case/underscore conventions are insufficient.
- Ripwire impact is a transitive-reachability floor, never authoritative change
  impact. Human/design annotations are non-canonical by default.
- Runtime OBSERVED evidence must not be upgraded to static MUST.
- Canonical Evidence = code-derived truth (OBSERVED facts). Design / Suggested / Flow Region are NOT Canonical Evidence.
- PARTIAL != COMPLETE. UNKNOWN != FALSE. Never upgrade or guess.
- An unresolved call target must stay UNKNOWN — do NOT invent the real function.
- Flow (MasterFlow / Scenario / Region) is a DERIVED projection, not evidence.
- One canonical symbol may belong to many Flows — never duplicate identity.
- Prefer drilling any high-level claim down to a witness (fact_id + file/line), then to source.
- If evidence is insufficient, answer UNKNOWN/PARTIAL explicitly.

Data-interface truth rules (DATA-INTERFACE0 derived objects):
- A DataPort describes a program interface, not necessarily a physical memory transfer.
- SYMBOLIC shape is known symbolically; it is not UNKNOWN.
- PARTIAL shape is not COMPLETE.
- A C pointer is not automatically a vector or matrix.
- A non-const pointer is not automatically OUTPUT.
- PASS_BY_REFERENCE is not evidence of a memory copy.
- byte_size_expression is not an observed transfer volume.
- Flow/Module interfaces are derived projections and must preserve Function Port witnesses.
- Port IDs are DataPort identities, never canonical function IDs.

Contract notes:
- For Flow exploration, discover identifiers first:
  query_flow(action="list") → query_flow(action="regions", flow_id=...) → drill down.
  Do not guess flow_id or region_id. Unknown ids return candidates + hints (recover
  on the NEXT call); never invent ids that are not in the returned lists.
- query_flow(action="get") returns summaries + previews only; full guards come with
  include_guards=true (paged via cursor), full stages via action="semantic".

Working style:
1. Start with repo_status to see what Rintel knows about the active repo/lane (includes data_interface_capability).
2. Use search_symbols / query_topology / find_path with a DEFAULT limit; take bigger payloads via read_resource only when needed. search_symbols searches the frozen command-layer lanes AND falls back to the DATA-INTERFACE0 parse set + kernel universe (rows carry a `lane`/sources marker), so a 0-match is meaningful; still confirm existence with get_symbol before asserting a symbol is absent.
3. Prefer building answers layer by layer; keep tool calls minimal.
4. For function/subroutine ports: get_symbol(canonical_id, include_ports=true) or query_data_interface(action=ports, function=...); compact ports_summary is returned by default — request the full array only when the answer needs it.
5. For Port-to-Port evidence: explain_evidence(binding_id=...) drills caller actual → callee formal port; query_topology with DATA/incl. data interfaces exposes edge-level port_bindings, and NEVER infers ports by name (UNKNOWN stays UNKNOWN).
5d2. Runtime data (RUNTIME-DATA0): query_runtime actions data/data_access/data_lineage/dgesv expose OBSERVED data objects over the CRM solver chain (bmatrix shared static buffer regions matrix/x/a/b/ipiv/rex + LBLOCK.nb STATE) with runtime RESOLVED shapes (dim n from probes, DGESV M). A logical_size is dtype*elements — NEVER a transferred byte count (no reads of memory-copy volume are possible here). DGESV A/B are LOCAL cursors into the shared bmatrix buffer (not Ports): data flows via STATE/static buffer, not function parameters (bmatrix -> BlockPopulation -> f_dgesv -> dgesv_.A). observed READ/WRITE assignment ordered by probe sequence; observed_last_writer is NOT a static MUST.
5d. Runtime evidence (RUNTIME-TRACE0): query_runtime observes what a REAL instrumented FAC run executed (trace backend = -finstrument-functions; CALL/RETURN paired; integrity 'PARTIAL' with unclosed_at_end=5 means only the legitimate exit() unwind frames). OBSERVED != static MAY: an edge that never fired this run (alignment NOT_OBSERVED_THIS_RUN, e.g. rate-coeff -> DRBranch in W1/W2) must be reported as 'static possible, not observed in this run' — NEVER as impossible/unreachable; and an absent symbol in the trace does not disprove its static existence. Distinguish run workload: rate-config 11 calls (W1) vs 13 (W2); solver chain LevelPopulation -> BlockPopulation x2 -> DGESV x2 (verifiable file:line via invocation rows).
5c. Error contract (MCP-ERROR-CONTRACT-FIX0): arguments are validated against the
    tools/list schema at the boundary. A failed call returns a NORMAL tool result with
    summary + error {code, message, parameter?, received?, did_you_mean?, allowed?,
    required?, candidates?, hint?}. Recover ON THE NEXT CALL: use error.did_you_mean /
    error.hint to fix the call — never repeat the same invalid call, never invent values
    outside allowed/required/candidates, and never treat an error result as an answer to
    the question. Codes: UNKNOWN_ARGUMENT (param not in schema), MISSING_REQUIRED_ARGUMENT
    (required/one-of missing), INVALID_ARGUMENT_TYPE, INVALID_ENUM (allowed lists the
    enum), AMBIGUOUS_ARGUMENTS (exactly-one selector rule), SYMBOL_NOT_FOUND /
    FUNCTION_NOT_FOUND / MODULE_NOT_FOUND / EDGE_NOT_FOUND / *_NOT_FOUND (requested +
    candidates + hint), INVALID_URI (read_resource scheme). query_topology edge_id values
    are kind-prefixed (CALL:SRC->TGT); explain_evidence(edge_id=...) accepts them, and a
    non-direct edge recovers via its witness_fact_ids (call explain_evidence(fact_id=...)).
5b. For cross-library call chains (command → faclib → BLAS/LAPACK): get_symbol / query_topology responses carry kernel_universe + kernel_calls (faclib coverage universe: definition files, lines, macro-linkage evidence e.g. cfortran DGESV→dgesv; kernel_calls lists an engine symbol's resolved callees with file:line — follow it step by step for inter-library chains). Resolution is EXACT/CANDIDATE_SET/UNKNOWN only — never invent a target; report UNKNOWN when evidence is insufficient (function-pointer registry targets: the string command name is NOT the function target).
6. For designs: create_design → design_patch(mode='preview') → design_patch(mode='apply') → validate_design. Never touch canonical evidence.

Tool exposure (RINTEL-DSH0):
- This deployment may expose only a SUBSET of the tools listed above (a read/analyse
  surface without the design tools). tools/list is the authority for what you can
  call; a tool named here but absent from your catalog is NOT exposed — say so
  instead of inventing an equivalent, and never reach for it another way.
"""
