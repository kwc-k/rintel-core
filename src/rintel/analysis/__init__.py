"""FLOW1-ANALYZER0: deep semantic provider tournament package.

Frozen surface:
- contract.py      — AnalysisFact / EvidenceClass / ProviderCapabilities
- provider.py      — AnalysisProvider ABC + Scope
- graphkernel.py   — rustworkx-backed GraphKernel (§16)
- joern.py         — JoernProvider (sidecar, fixed operations)
- cpgqls.py        — FraunhoferCPGProvider
- lfortran.py      — LFortranProvider (ASR)

Truth boundary (frozen): AnalysisFact is the only way into
Evidence/Flow/DRC; INFERRED/HEURISTIC never become canonical observed facts.
"""
