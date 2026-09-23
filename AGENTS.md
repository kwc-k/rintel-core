# Rintel development charter

This file governs repository work by humans and agents. Keep it lightweight; task-specific acceptance contracts still decide what evidence is sufficient.

## Git and release boundaries

- `v0.1.0` is immutable. Do not move its tag, develop on a detached release tag, or rewrite published history.
- `main` accepts changes through pull requests. Do not push directly, force-push, or delete it. Its protection requires a PR but does not yet require CI checks or an approving reviewer; record the tests actually run in each PR.
- For ordinary work, branch from an up-to-date `main` unless a task freezes a different base. Verify the remote and base SHA before branching; use `git pull --ff-only`, never a silent rebase/reset of another person's work.
- One agent/task owns one branch and worktree. Do not share a mutable datastore between concurrently running agents. Split branches only where changes can be independently implemented, tested, and merged.
- Preserve authored commits and evidence in integration. Prefer `git merge --no-ff` for the Guided Mission lanes; do not squash frontend and backend into one untraceable commit.
- Keep generated databases, indexes, browser profiles, secrets, and user repositories out of commits.

## Guided Mission branch sequence

The common starting commit is `4f27dc2e106ef8cfdeeb66c91333728e88dcc5c5` (`v0.1.0` release commit). `guided-mission/integration` is the candidate integration branch, not an agent's daily coding branch. `guided-mission/integration-map` starts from that same commit and targets `guided-mission/integration`, not `main`.

1. **Integration Map first.** `GUIDED-MISSION-INTEGRATION-MAP0` inventories existing Vue components, backend APIs/services, W1/W2/W4 and E2E witnesses; labels each `REUSE`, `HOOK`, `SMALL_CHANGE`, `NEW`, or `OUT_OF_SCOPE`; freezes the Mission API contract; and audits 1280/1440/1800px. This stage should not change production behavior. Its evidence and map commit must be `PASS / FROZEN` before implementation branches start.
2. **One shared map base.** Record the exact frozen `MAP_SHA`. Create `guided-mission/frontend` and `guided-mission/backend` worktrees from that same commit, not from moving branch tips. Do not redefine the contract independently in either lane.
3. **Lane ownership.** Frontend owns Mission Strip/Drawer, Context Cue, Proof Card, alignment presentation, responsive/accessibility behavior, and tutorial UI hooks. Backend owns MissionSpec/Evaluator, progress/workspace state, goal predicates, witness binding, isolation, and mission-state API. Frontend clicks must not locally assert mission completion; backend must compose existing authority/query services, not add a parallel Tutorial Truth/Alignment authority.
4. **Focused evidence.** Frontend runs `pnpm --dir web test`, `pnpm --dir web typecheck`, `pnpm --dir web build`, and focused UI tests. Backend runs MissionEvaluator, truth-boundary, isolation, and existing authority/alignment regressions. Record exact outcomes. The known time-bounded full backend suite is not PASS evidence and is not required for every small lane change.
5. **Integration.** After both lanes pass, merge into `guided-mission/integration` with separate merge commits, normally backend then frontend. Run integration contracts, `GUIDED-MISSION-INTEGRATION1`, real browser Guided Mission E2E, and fresh-user transfer on the combined candidate.
6. **Promotion to main.** Only a `guided-mission/integration` PR may carry the tutorial line to `main`, after Integration Map, both focused lanes, truth-boundary, isolation, 1280/1440/1800 UI, browser mission, and fresh-user gates have explicit PASS evidence. A missing or partial gate stays visible; do not relabel it PASS.

Do not start successor stages merely because their branches exist. A branch or PR is a workspace boundary, not acceptance evidence.
