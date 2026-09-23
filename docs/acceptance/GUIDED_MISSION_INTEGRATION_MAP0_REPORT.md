# GUIDED-MISSION-INTEGRATION-MAP0

**Verdict: PASS / FROZEN — integration map only.** This is a decision-complete implementation map, **not** acceptance of `GUIDED-MISSION-UX0` implementation. No tutorial feature or production behavior was changed. The exact `MAP_SHA` is the Git commit containing this report and its linked machine-readable map; record that commit in the PR and handoff, not self-referentially inside this file.

## Frozen inputs and evidence boundary

- Public repository: `https://github.com/kwc-k/rintel-core`; `main`/`v0.1.0` release base: **`4f27dc2e106ef8cfdeeb66c91333728e88dcc5c5`**. `guided-mission/integration-map` started from this SHA; its earlier charter-only commit is `3387a33e635bbacc10ce4f6adfe15445792ba6a4`. `guided-mission/integration` is a candidate branch, not release or implementation evidence.
- Requirement authority: user-supplied complete `GUIDED-MISSION-UX0`, `DESIGN_FROZEN`, on 2026-09-23. Implementation/acceptance were explicitly `NOT STARTED`. This report maps that text, including TUX1–TUX18; it does not silently revise it.
- Code authority: tracked `v0.1.0` Python/Vue files in this public worktree. The full local Rintel source workspace supplies historical acceptance reports, original W1/W2/W4 real artifacts and focused tests. `cross_layer_alignment.py`, `build_recovery.py`, and `ChangeWorkspaceView.vue` were byte-equal between those workspaces at audit time. Historical artifacts are **not** in the 354-file public release and cannot be presented as a clean-install product witness.
- Focused historical backend verification: 14 tests passed for exact W1/W2/W4 alignment and ExecutionAuthority; one Starlette deprecation warning. No Guided Mission implementation or full-suite PASS is claimed. [Validation record](../../analysis_tournament/guided_mission_integration_map0/validation.json).
- Exact per-requirement fields—`requirement`, existing implementation, location, classification, required change, owner, risk and test—are frozen in [integration_map.json](../../analysis_tournament/guided_mission_integration_map0/integration_map.json). Every row has one primary `REUSE`, `HOOK`, `SMALL_CHANGE`, `NEW`, or `OUT_OF_SCOPE` label. A row labeled `NEW` may still *compose* existing authoritative services.

## Minimal integration boundary

| Area | Reuse unchanged | Only new/hook work | Forbidden shortcut |
| --- | --- | --- | --- |
| Canonical/Evidence/Coverage | Published graph, admission support, exact SourceSpan, `applicable_complete` certificate validation | Bound inspection receipt and witness provisioning | `TutorialTruthService`, vote-based truth, absence-as-MISSING |
| Design/Alignment | `DesignLifecycleService`, DesignRevision, `workspace_projection`, `project_change_alignments` | Session-bound projection adapter and three-column presentation | `TutorialAlignmentService`, frontend alignment computation |
| Runtime | `derive_runtime`, bounded W1/W2/W4 trace contract | Load genuine source/binary/build/revision-bound artifacts into isolated tutorial scope; pass session-owned `run_artifacts` or a narrowly configurable directory | Old FAC trace promoted to exact W1/W2/W4; detached JSON fixture |
| Build/Test/Repair | `ExecutionAuthority`, trusted TestRunReceipt, `BuildRecoveryService.assess_repair` | Server-owned tutorial profiles bound to tutorial worktree | Browser-selected shell command; Build PASS or Agent claim ⇒ `VERIFIED_REPAIR` |
| Git | Real Git worktrees, scoped repository checks, `GitWorkspaceManager` patterns | Tutorial-owned repo/worktree/datastore provisioning | Work in user's production repo/worktree; merge ⇒ Canonical CURRENT |
| UI | Explorer/Canvas/Inspector/Dock, Change Workspace, bound source, raw diagnostic views | Mission host/strip/drawer/cue/proof, responsive fix, precise answer form | Separate scripted wizard, click-sequence completion, fake evidence |

This is a presentation/onboarding layer over the real product, not a new top-level truth plane. The source code already keeps Design, Static and Runtime independent: static `PRESENT` needs admitted support; static `MISSING` needs an applicable production `COMPLETE` certificate; runtime `NOT_OBSERVED` needs bounded complete trace coverage. `UNKNOWN`, `MISSING`, `NOT_OBSERVED` and `UNMEASURED` remain distinct. The existing Change Workspace shows all five evidence dimensions, but its alignment row is mostly prose and is not yet the frozen three-column tutorial presentation.

### Mission A — W1

`Design EXPECTED / Static PRESENT / Runtime OBSERVED` follows the real historical chain: retained C source and AppleClang build → published canonical relation with Clang admission support → exact endpoint bridge → real bounded run `run-rae0-direct-true-b9233be63e81` → backend `project_change_alignments` / Change Workspace → bound Evidence Inspector and SourceSpan. The original `RUNTIME_ALIGNMENT_EVIDENCE0` acceptance report and test verify the bound result `IMPLEMENTED_AND_OBSERVED`. Mission success additionally requires a server-validated user conclusion and a genuine evidence/source inspection receipt; reaching a button or tab is not proof of understanding. Any legitimate navigation path may lead to those reads.

### Mission B — W2 and W4

W2 reuses the same published static relation and binary as W1; its different bounded workload `run-rae0-direct-false-4e5c92b1c34c` has complete relation-domain trace coverage and supports only `NOT_OBSERVED` in that run/window, **not** `NEVER_EXECUTED`. W4 uses real indirect call `A_indirect→B`, run `run-rae0-indirect-ae88371d88a9`: runtime `OBSERVED` while static remains `UNKNOWN` because indirect-domain coverage is partial. Existing backend results are `IMPLEMENTED_RUNTIME_NOT_OBSERVED` and `STATIC_ANALYSIS_GAP_WITH_RUNTIME_OBSERVATION`. The evaluator rejects a user conclusion of static `MISSING` for W4 and returns the actual missing/partial certificate reason; it never manufactures a negative fact. The first mismatch may show a cue; the second begins without one, but hints remain requestable.

### Mission C — isolated real repair

The real chain is tutorial-owned Git repository/worktree and SQLite datastore → `DesignLifecycleService.open_change/plan/begin_implementation` → bounded source edit → server-owned BUILD then trusted TEST `ExecutionReceipt`/`TestRunReceipt` → actual isolated reindex job and adoption → Expected-vs-Actual/LVS/backend Alignment → `VerifyChange` → `BuildRecoveryService.assess_repair == VERIFIED_REPAIR`, with all source, design and canonical revisions matching. The full source workspace's `EXECUTION_AUTHORITY0` real C witness proved this chain; a later historical `BUILD_RECOVERY0` report alone did *not* (it stopped at `BUILD_FIXED_BUT_TEST_AUTHORITY_UNAVAILABLE`). The tutorial must construct/package its own runnable bounded source and profile; no user production path is allowed. A real reindex may publish a revision **inside the tutorial datastore**, but neither Mission completion nor a Git commit/merge publishes the production canonical revision.

`LOCAL_OWNER_AUTH0`, `APPROVAL-ISSUANCE-ANCHOR0` and `COMMAND-GATE-CLOSURE0` are **not closed as a production approval/publication line**. Mission C excludes production approval, close and publication. For the tutorial `approval_required` remains false; no UI text may imply its isolated `VERIFIED_REPAIR` is production authorization. Mission D/multi-Agent Git is outside first-run A/B/C.

## Frozen Mission API and domain contract

The following is the **implementation contract**, not an implemented endpoint. Version all specifications and predicates; reject unknown versions. Identifiers bind a session, tutorial datastore, repository, immutable design revision, canonical revision, source/binary/build identities, exact relation and run scope. Client input is an attempted conclusion, never an authoritative state.

```ts
type AssistanceLevel = 'GUIDED' | 'ASSISTED' | 'INDEPENDENT'
type CompletionStatus = 'NOT_STARTED' | 'ACTIVE' | 'PAUSED' | 'COMPLETED' | 'SKIPPED' | 'BLOCKED'
type Milestone = 'EVIDENCE_FOUND' | 'ALIGNMENT_UNDERSTOOD' | 'CONCLUSION_PENDING' | 'PROOF_ESTABLISHED'

interface MissionSpec {
  schema: 'mission-spec/1'; id: 'A' | 'B' | 'C' | 'TRANSFER'; version: string
  title: string; objectives: Objective[]; witness_manifest_digest: string
  success_predicate: SuccessPredicate; allowed_scope: string[]
}
interface Objective {
  id: string; statement: string; success_explanation: string
  concept_ids: string[]; optional_cue_target?: string
}
interface SuccessPredicate {
  id: string; version: string; required_backend_states: string[]
  required_inspection_kinds: Array<'EVIDENCE' | 'SOURCE' | 'ALIGNMENT' | 'BUILD' | 'TEST'>
  required_conclusion_kind: string
}
interface TutorialWorkspaceRef {
  session_id: string; tutorial_repo_id: string; worktree_id: string
  datastore_identity: string; source_commit: string; allowed_scope_digest: string
}
interface HintState {
  assistance: AssistanceLevel; requested_hint_ids: string[]
  cue_target_id: string | null; paused: boolean
}
interface RemainingUnknowns {
  dimension: 'DESIGN' | 'STATIC' | 'RUNTIME' | 'BUILD' | 'TEST' | 'AUTHORITY'
  state: string; reason: string; bounded_scope: string | null
}
interface Proof {
  schema: 'mission-proof/1'; id: string; mission_spec_version: string
  predicate_version: string; tutorial_session_id: string; evaluated_at: string
  established: Array<{dimension: string; state: string; scope: string}>
  supporting_refs: string[]; inspection_receipt_ids: string[]
  remaining_unknowns: RemainingUnknowns[]; limitations: string[]
  binding: {tutorial_repo_id: string; design_revision: string | null
    canonical_revision: string; relation_id: string | null
    runtime_run_ids: string[]; source_digest: string | null
    binary_sha256: string | null; build_identity: string | null}
}
interface MissionState {
  schema: 'mission-state/1'; session_id: string; spec_id: MissionSpec['id']
  spec_version: string; state_version: number; completion: CompletionStatus
  current_objective_id: string; milestones: Milestone[]; hint: HintState
  workspace: TutorialWorkspaceRef; proof: Proof | null
  remaining_unknowns: RemainingUnknowns[]
}
```

The initial API surface is limited to: `GET /api/v1/tutorial/missions` (read-only versioned specs), `POST /api/v1/tutorial/sessions` (allocate isolated session), `GET /api/v1/tutorial/sessions/{id}` (server state), `POST /api/v1/tutorial/sessions/{id}/control` (`pause|resume|restart|skip|hint|assistance` on tutorial state only), bound `GET /api/v1/tutorial/sessions/{id}/inspect/{evidence|source|alignment}` (delegate existing read services, issue inspection receipt only on successful exact read), and `POST /api/v1/tutorial/sessions/{id}/evaluate` (attempted structured conclusion plus inspection receipt IDs and `state_version`; returns updated state/proof or reasoned denial). Mission C's bounded actions use session-bound adapters over the **existing** lifecycle, Git, execution and build services with server-owned profiles; they must not expose arbitrary commands or direct production repository IDs. These adapter paths may be grouped under the session resource without expanding the truth contract.

An `evaluate` request cannot supply `PRESENT`, `MISSING`, `OBSERVED`, `COMPLETE`, `VERIFIED_REPAIR`, a canonical revision, or a proof as facts to accept. It only selects a conclusion and existing receipt IDs. The backend re-reads live, exact-bound state, checks revision freshness, verifies inspection receipts were minted by its own successful reads for this session, and evaluates the versioned predicate. A click sequence, client localStorage, timed stall, or route visit may request a hint or read, **never** mark a milestone complete. For Mission A, the bound W1 alignment plus inspected admissible support/SourceSpan and a correct scoped conclusion are required. For B, both W2 and W4 scoped interpretations are required. For C, the isolated `VERIFIED_REPAIR` derivation and required lifecycle/test/reindex bindings are required. The separate TRANSFER case, not W1/W2/W4 replay, gates `INDEPENDENT`.

`Proof` and `TutorialProgress` are tutorial receipts/state only, physically separated from canonical admission, DesignRevision and authority stores. Their `supporting_refs` reference authoritative objects; they do not become those objects. Fail closed on missing bundle, changed compiler/source/binary/build identity, stale canonical/design revision, missing COMPLETE coverage, wrong tutorial session or production-path escape.

## Frozen frontend and backend change sets

**Frontend:** one route-independent Mission host; compact strip outside the crowded TopBar; dismissible responsive drawer; single optional cue; evidence-backed Proof Card; structured conclusion form and client for the frozen API; three independent Design/Static/Runtime cells with interpretation below; first-run and later Help entry; exact Evidence→SourceSpan drilldown; process-stage rendering from real jobs/receipts; localized accessible text; bounded 1280px TopBar collision fix. Reuse existing Explorer, Canvas, Inspector, Dock, Change Workspace, raw evidence and diagnostics. No local `missionComplete` truth state.

**Backend:** versioned readonly `MissionSpec`; thin `MissionEvaluator` composing existing cross-layer, support, coverage, lifecycle, execution and build services; tutorial-only persisted `TutorialProgress` and immutable `Proof`/inspection receipts; `TutorialWorkspace` allocates and guards own Git repo/worktree, SQLite datastore and artifact/profile roots; checksum-verified, reproducible W1/W2/W4 and transfer witness provisioning; narrow session-bound runtime-artifact injection rather than changing alignment semantics; session-bound Mission C adapters using existing services and server-owned profiles; exact-bound API with fail-closed input/version checks. No new analyzer, parser, truth/coverage authority, RPP change or general shell execution.

The isolated datastore requirement is a real integration seam: current HTTP handlers use app-level `get_store`, so merely adding a tutorial `repo_id` to the production database would violate the frozen UX. The tutorial API must instantiate/bind the existing services to a per-session store (or a proven equivalent isolated local instance), not route tutorial mutations through the app's production store. `project_change_alignments` currently defaults to source-tree `analysis_tournament` run directories; a narrow explicit session-owned artifact input is required. Both are implementation prerequisites, not changes made in this map node.

## Viewport audit

Measured with headless Chrome, real loopback `v0.1.0` server, isolated SQLite datastore, indexed real Git checkout and a test-only DesignChange; viewport height 900. [Machine-readable geometry and conditions](../../analysis_tournament/guided_mission_integration_map0/viewport_audit.json):

| Width | Root Explorer / center / Inspector | Change nav / main / Inspector | Existing top navigation |
| --- | --- | --- | --- |
| 1280 | 300 / **588** / 360 px; dock 300px high | 220 / **730** / 330 px; panel 674px | **FAIL:** selected-repo `Design Changes` center hit language toggle |
| 1440 | 300 / **748** / 360 px | 220 / **890** / 330 px; panel 834px | Click center works; button and language group edge-overlap ~24px |
| 1800 | 300 / **1108** / 360 px | 220 / **1250** / 330 px; panel max 1000px | No collision observed |

With the real Evidence Inspector open, a fourth permanent ~300px drawer would leave only ~430px Change main at 1280 and ~590px at 1440. Therefore Mission Drawer must be transient/overlay or replaceable, not a fixed new column; Mission Strip must not be appended to the existing TopBar. This audits **existing** layout, not future mission UI PASS. The frontend branch must retest all widths with selected repository, Inspector open, Strip and Drawer both states, keyboard focus and language/Design Changes reachability. The 1280 defect predates Mission and is part of the bounded frontend change set.

## Gate disposition, blockers and branch plan

The map is PASS because each frozen UX item has a primary classification and owner, every A/B/C chain resolves to existing backend authorities, the missing install-time witness/test data and real isolation seam are explicit `NEW` work, the API prevents client truth promotion, and the three requested viewport risks are measured. It does **not** mean Guided Mission itself works. [Issue register](../../analysis_tournament/guided_mission_integration_map0/issue_register.json) holds five implementation prerequisites/limits. None is hidden as a map success: notably the public release contains no historical W1/W2/W4 assets or tests, and Mission C isolation does not exist yet. If the implementation cannot construct exact witnesses or enforce physical isolation, it must fail its own gates rather than weakening this map.

No feature branch/worktree is created in this node. After the map commit and PR to `guided-mission/integration` are accepted as the single frozen `MAP_SHA`, create both `guided-mission/backend` and `guided-mission/frontend` from **that identical immutable SHA**, each in its own worktree and datastore. Backend owns Mission service/isolation/witness/API; frontend owns UI presentation/hooks. Focused backend authority/mission/isolation and frontend unit/typecheck/build/UI tests run separately; historical full-suite incompletion is never called PASS. Merge with separate commits into `guided-mission/integration` (normally backend first), then run combined contract, browser A/B/C, three widths, keyboard, independent transfer and fresh-user zero-critical-truth-error acceptance. Only then PR integration to `main`. Do not touch `v0.1.0` or start Guided Mission feature implementation before this map is accepted.

**STOP:** `GUIDED-MISSION-INTEGRATION-MAP0` only. No production approval/publication, Mission D, new analyzer, truth redesign or feature implementation was started.
