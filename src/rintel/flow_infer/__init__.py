"""FLOW-INFER0: FAC scientific calculation flow inference (backend).

READ-ONLY consumer of the frozen FAC topology artifact + the real FAC
source tree.  Everything this module emits is DERIVED/PROJECTED — never
canonical evidence (FI1).  Nothing here mutates AnalysisFact, invents CALL,
repairs DATA, or upgrades truth (FI2/FI5).  Capability language is carried
verbatim from the frozen lane (C cross-procedural DATA = PARTIAL).
"""
