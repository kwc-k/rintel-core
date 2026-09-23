# Rintel Guided Mission context

Guided Mission is a tutorial presentation context over existing Rintel evidence and workflow authorities. These terms distinguish tutorial state from the product facts it helps users inspect.

## Language

**MissionSpec**:
A versioned, read-only goal contract that binds objectives, a real witness manifest and a server-owned SuccessPredicate.
_Avoid_: click script, tour step

**MissionState**:
The server-owned state of one tutorial session, including its current objective, assistance level and completion status; it is not evidence.
_Avoid_: canonical state, UI click state

**SuccessPredicate**:
A versioned backend rule that checks exact-bound Rintel state, admissible inspection receipts and a user's scoped conclusion.
_Avoid_: frontend completion flag

**Proof**:
An immutable tutorial evaluation receipt citing existing authoritative facts and explicitly retaining their limits and unknowns.
_Avoid_: canonical support receipt, truth promotion

**RemainingUnknowns**:
Scoped evidence limits left after a mission step, each tied to the Design, Static, Runtime, Build, Test or Authority dimension.
_Avoid_: failure count

**HintState**:
Tutorial-only assistance choices and optional cue state; hints never establish a fact or complete an objective.
_Avoid_: evidence status

**TutorialWorkspace**:
An isolated tutorial-owned Git source/worktree, datastore and artifact/profile boundary used for real but non-production investigation and repair.
_Avoid_: production workspace, UI-only fixture

**InspectionReceipt**:
A tutorial-only record that an exact, session-bound evidence, alignment or SourceSpan read succeeded through a server-owned path.
_Avoid_: click receipt, canonical admission

**CompletionStatus**:
The lifecycle of a tutorial session, separate from DesignChange state, Build/Test results, canonical publication and approval.
_Avoid_: overall truth, production approval
