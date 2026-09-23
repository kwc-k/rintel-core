"""FLOW-SEMANTIC0: human-semantic projection over frozen Rintel artifacts.

Layer purity (never mutates the frozen planes):
  Evidence Plane     = code truth            (unchanged)
  Flow Projection    = derived truth         (FLOW-INFER0, unchanged)
  Semantic Flow      = annotation / interpretation   (THIS module)
  Design Plane       = desired future state  (unchanged)

Every SemanticStage/SemanticEdge is a DERIVED, HEURISTIC annotation with
origin=RULE_BASED and status=SUGGESTED until accepted by a human (§2/§4/§17).
Classification requires evidence signals (call topology, callee lists,
DATA-INTERFACE0 parse-set membership, port evidence, witnesses); a name by
itself is only a weak hint (§7).  Kinds come from the small §5 ontology only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOURNAMENT = REPO / "analysis_tournament"
OUT = TOURNAMENT / "flow_semantic0"
FIN = TOURNAMENT / "flow_infer"
DI = TOURNAMENT / "data_interface"
KERNEL_OUT = TOURNAMENT / "semantic_substrate1"

KINDS = ("INPUT", "INITIALIZATION", "CONFIGURATION", "PREPARATION", "COMPUTE",
         "SOLVER", "TRANSFORM", "ITERATION", "VALIDATION", "POSTPROCESS",
         "OUTPUT", "DISPATCH", "SHARED_CORE", "OTHER")

# weak name-prefix hints (names are hints; the classifier adds evidence)
PREFIX_HINTS = {
    "INITIALIZATION": ("init", "reinit", "load", "read", "parse"),
    "CONFIGURATION": ("config",),
    "PREPARATION": ("set", "optimize", "structure", "build", "make", "prepare"),
    "COMPUTE": ("rmatrix", "compute", "calc", "solve", "dgemm", "dger", "dsbev", "dsteqr"),
    "TRANSFORM": ("rec", "transform", "couple", "rotate"),
    "POSTPROCESS": ("transition", "post", "analyze", "report"),
    "OUTPUT": ("save", "write", "print", "table", "dump", "exit"),
    "SOLVER": ("dsbev", "dsteqr", "dger", "dgemm", "solve"),
    "OTHER": ("free", "cleanup", "join", "merge"),
}


@dataclass
class SemanticStage:
    semantic_stage_id: str
    flow_id: str
    display_name: str
    description: str
    stage_kind: str
    scenario_id: str | None = None
    members: dict = field(default_factory=dict)      # flow_region_ids / canonical_symbol_ids / node_ids
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    truth_class: str = "HEURISTIC"                   # HEURISTIC | INFERRED
    origin: str = "RULE_BASED"                       # HUMAN | AGENT | RULE_BASED
    status: str = "SUGGESTED"                        # SUGGESTED | ACCEPTED | REJECTED
    evidence_refs: list = field(default_factory=list)
    coverage: str = "PARTIAL"
    unknowns: list = field(default_factory=list)
    layout_order: int = 0

    def to_dict(self):
        return asdict(self)


@dataclass
class SemanticMembership:
    semantic_stage_id: str
    member_kind: str          # flow_region | canonical_symbol | flow_node
    id: str
    why: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class SemanticEdge:
    edge_id: str
    source_stage: str
    target_stage: str
    kind: str                 # CONTROL | DATA
    witness_refs: list = field(default_factory=list)   # flow edge ids / binding ids / fact ids
    truth_class: str = "HEURISTIC"
    coverage: str = "PARTIAL"
    note: str = ""

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# evidence helpers
# ---------------------------------------------------------------------------

def _load(name: str, prefix: str, folder: Path):
    p = folder / f"{prefix}_{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _di_functions(di_dir: Path | None = None) -> dict[str, dict]:
    """name -> {file, ports} from the DATA-INTERFACE0 parse set.

    di_dir overrides the canonical lane (a staged sync compute must read the
    staged data-interface plane, never the previous canonical snapshot).
    """
    out = {}
    for f in (_load("ports", "fac", di_dir or DI) or []):
        out[f["name"]] = {"file": f.get("file"), "line": f.get("line"),
                          "ports": f.get("ports", [])}
    return out


def _hint_score(names: list[str], prefixes: tuple[str, ...]) -> tuple[int, list[str]]:
    hits = []
    for n in names:
        tok = re.split(r"[^A-Za-z0-9*]+", n)
        if any(p in tok or any(t.lower().startswith(p) for t in tok if len(t) >= 3)
               for p in prefixes):
            hits.append(n)
    return len(hits), hits


def _port_interface(ports: list[dict]) -> tuple[list[str], list[str]]:
    """inputs/outputs lines from DATA-INTERFACE0 (never re-derive shapes —
    only reference what the ports carry)."""
    ins, outs = [], []
    for p in ports:
        line = (f"{p.get('name')}: {p.get('dtype')}[{p.get('rank') if p.get('rank') is not None else '?'}]"
                f" {p.get('shape')} ({p.get('shape_status')})")
        d = p.get("direction")
        if d in ("INPUT", "INOUT"):
            ins.append(line)
        if d in ("OUTPUT", "INOUT"):
            outs.append(line)
    return ins, outs


# ---------------------------------------------------------------------------
# classification (rule based; name = hint, evidence required)
# ---------------------------------------------------------------------------

def classify_region(flow_id: str, region: dict, di: dict[str, dict]) -> dict:
    """→ stage-kind / display_name / description / evidence_refs / unknowns."""
    rid = region["region_id"]
    names = list(region.get("methods", [])) + list(region.get("callees", []))
    lnames = [n.lower() for n in names]
    evd: list[str] = []
    unknowns = []
    # strong signal: DATA-INTERFACE0 parse-set membership among callees/handlers
    di_hits = [f for f in di if f.lower() in lnames
               or any(f.lower() in n for n in lnames)]
    for f in di_hits:
        evd.append(f"DATA-INTERFACE0 parse set: {f} ({di[f]['file']})")
    # region provenance (frozen flow_infer why-lines = call connectivity evidence)
    for w in (region.get("why") or [])[:2]:
        evd.append(f"FLOW-INFER0 region why: {w}")
    unk = region.get("unresolved_callee_count", 0)
    if unk:
        unknowns.append(f"{unk} unresolved callee(s) outside the frozen lane")

    def pick(*kinds: str) -> tuple[str, str, str]:
        """choose the best matching kind family (highest hint score)."""
        scored = []
        for k in kinds:
            pre = PREFIX_HINTS.get(k, ())
            n, hit = _hint_score(names, pre)
            scored.append((n - len(set(hit)) * -0 / 1, k, hit))  # n = count
        scored.sort(key=lambda x: (-x[0], x[1]))
        n, k, hit = scored[0]
        return k, n, hit

    # deterministic family decisions (documented per region in the audit)
    base = region["name"].lower()
    if base.startswith("config"):
        return {"kind": "CONFIGURATION", "name": "计算配置", "evd": evd, "unk": unknowns,
                "text": "解析计算模式参数并把配置写入 FAC 配置库 (AddConfigToList 有端口证据)"}
    if base.startswith("reinit"):
        return {"kind": "INITIALIZATION", "name": "重初始化/复位", "evd": evd, "unk": unknowns,
                "text": "重载配置/数据库/激发的初始化命令族 (Reinit* 方法族)"}
    if base.startswith("rmatrix"):
        return {"kind": "COMPUTE", "name": "R-矩阵计算链", "evd": evd, "unk": unknowns,
                "text": "驱动 R-matrix 计算链的 6 个命令 (数值 kernel 在 frozen lane 之外)"}
    if base.startswith("rec"):
        return {"kind": "TRANSFORM", "name": "状态重耦合", "evd": evd, "unk": unknowns,
                "text": "对状态进行重耦合/变换 (RecoupleRO callee 证据)"}
    if base.startswith("transition"):
        return {"kind": "POSTPROCESS", "name": "过渡与后处理", "evd": evd, "unk": unknowns,
                "text": "计算并输出过渡/自旋禁戒表 (SaveTransitionEB callee 证据)"}
    if base.startswith(("cetable", "ci")):
        return {"kind": "OUTPUT", "name": "结果表输出", "evd": evd, "unk": unknowns,
                "text": "生成 CI/CE 表并保存 (SaveExcitationEB callee 证据)"}
    if base.startswith("free"):
        return {"kind": "OTHER", "name": "清理/释放", "evd": evd, "unk": unknowns,
                "text": "释放内存/激发/矩数组的清理命令族 (Free* 方法族)"}
    if base.startswith("join"):
        return {"kind": "OTHER", "name": "数据库合并", "evd": evd, "unk": unknowns,
                "text": "合并数据库/表 (JoinDBase/JoinTable callee 证据)"}
    if base.startswith(("set", "optimize", "structure")):
        return {"kind": "PREPARATION", "name": "计算参数设置", "evd": evd, "unk": unknowns,
                "text": "计算参数/网格/选项设置命令族 (Set*/Optimize*/Structure* 方法族)"}
    # default: unclassified (never force a name — §20)
    n, k, hit = pick(*KINDS)
    return {"kind": "OTHER", "name": "Unclassified Region", "evd": evd, "unk": unknowns,
            "text": "无法可靠归类 (语义证据不足); 保持 UNKNOWN 责任"}


# ---------------------------------------------------------------------------
# CRM-SEMANTIC1: evidence-driven stage plan for the CRM (collisional-radiative
# modeling) flow. Names/descriptions are derived from real witnesses in
# faclib/crm.c + faclib/rates.c (def lines, kernel CALL sites, data buffers);
# nothing is invented from domain common-sense (§15). All stages are
# SUGGESTED annotations (origin=SUGGESTED by default; user edits win).
# ---------------------------------------------------------------------------

def _crm_plan(regmap: dict) -> list[dict]:
    """Plan for app == 'crm' (flow master:crm:fac_c).

    Anchors: frozen flow node ids (cmd/crm:entry/init/dispatch/exit) and
    DI-parse-set canonical symbols (function:NAME) with port evidence.
    """
    SET_RATE_CMDS = ["cmd:crm:SetAIRates", "cmd:crm:SetAIRatesInner", "cmd:crm:SetCXRates",
                     "cmd:crm:SetCERates", "cmd:crm:SetCIRates", "cmd:crm:SetRRRates",
                     "cmd:crm:SetTRRates", "cmd:crm:SetRateMultiplier", "cmd:crm:SetRateAccuracy",
                     "cmd:crm:SetGamma3B", "cmd:crm:SetEleDist", "cmd:crm:SetEleDensity",
                     "cmd:crm:SetPhoDist", "cmd:crm:SetPhoDensity", "cmd:crm:SetAbund",
                     "cmd:crm:SetNumSingleBlocks", "cmd:crm:SetIteration", "cmd:crm:NormalizeMode",
                     "cmd:crm:SetCascade", "cmd:crm:SetStarkZMP", "cmd:crm:SetExtrapolate",
                     "cmd:crm:SetEMinAI", "cmd:crm:SetInnerAuger", "cmd:crm:SetCxtDensity",
                     "cmd:crm:SetCxtDist", "cmd:crm:SetCXLDist"]
    SET_RATE_SYMS = ["SetCERates", "SetCIRates", "SetRRRates", "SetTRRates", "SetAIRates",
                     "SetCXRates", "SetRateMultiplier", "SetRateAccuracy", "SetGamma3B",
                     "SetEleDist", "SetEleDensity", "SetPhoDist", "SetPhoDensity", "SetAbund",
                     "SetNumSingleBlocks", "SetIteration", "NormalizeMode", "SetCascade",
                     "SetStarkZMP", "SetExtrapolate", "SetEMinAI", "SetInnerAuger",
                     "SetCxtDensity", "SetCxtDist", "SetCXLDist"]
    UNCLASS_CMDS = ["cmd:crm:AddIon", "cmd:crm:EleDist", "cmd:crm:PhoDist", "cmd:crm:CxtDist",
                    "cmd:crm:InterpSpec", "cmd:crm:PrepInterpSpec", "cmd:crm:MemUsed",
                    "cmd:crm:MPIRank", "cmd:crm:PlotSpec", "cmd:crm:Print", "cmd:crm:PrintWallTime",
                    "cmd:crm:ReadKronos", "cmd:crm:SelectLines", "cmd:crm:SetOption",
                    "cmd:crm:SetUTA", "cmd:crm:System", "cmd:crm:WallTime"]
    # NOTE: cmd:crm:SetBornFormFactor / cmd:crm:SetBornMass are NOT listed here — they are
    # added by the SetBorn region's methods in the unclassified stage (no duplicate memberships).
    return [
        {"id": "stage:crm:init", "kind": "INITIALIZATION", "name": "输入/初始化与模式分派",
         "text": "程序入口 (main/crm:entry), 内部初始化 (InitCRM0 crm.c:208), 命令分发器 "
                 "(ParseArgs) 与重初始化 (ReinitCRM crm.c:412); MPI/字节序/ProcID 基础环境设置",
         "regions": [], "nodes": ["crm:entry", "crm:init", "crm:dispatch", "cmd:crm:ReinitCRM",
                                  "cmd:crm:CheckEndian", "cmd:crm:InitializeMPI",
                                  "cmd:crm:FinalizeMPI", "cmd:crm:SetProcID"],
         "symbols": ["InitCRM0", "ReinitCRM"],
         "evd": ["flow node crm:entry/init/dispatch in master:crm:fac_c",
                 "InitCRM0 def faclib/crm.c:208; ReinitCRM def faclib/crm.c:412"],
         "unk": ["命令分派守卫 (130 个 g-dispatch:* 守卫) 全部 UNKNOWN — 无脚本证据"]},
        {"id": "stage:crm:structure", "kind": "PREPARATION", "name": "原子结构/能级块准备",
         "text": "SetBlocks (crm.c:987) 读取原子结构 EN 文件 (ReadENHeader@1079/ReadENRecord@1124)"
                 " 并构建能级块 LevelBlock (SingleLevelBlock@1221/NewLevelBlock@1244/"
                 "CopyNComplex@1241), InitBlocks (crm.c:1756) 建立块结构运行时数组",
         "regions": [], "nodes": ["cmd:crm:SetBlocks", "cmd:crm:InitBlocks", "cmd:crm:MemENTable"],
         "symbols": ["SetBlocks", "InitBlocks", "FindLevelBlock", "SingleLevelBlock",
                     "NewLevelBlock", "CopyNComplex"],
         "evd": ["SetBlocks@987 callee 链: OpenFileRO@1063, ReadENHeader@1079/1118, "
                 "ReadENRecord@1124, JFromENRecord@1200/1285, VNIFromSName@1209/1294",
                 "FindLevelBlock def faclib/dbase.c:7852"]},
        {"id": "stage:crm:rate-config", "kind": "CONFIGURATION", "name": "速率与环境参数配置",
         "text": "Set*Rates 速率计算配置族与等离子体环境参数族 (SetEleDist/SetEleDensity/"
                 "SetPhoDist/SetPhoDensity/SetAbund/SetIteration/NormalizeMode 等); "
                 "RateCoefficients (crm.c:8987) 在计算前按序调用 SetCERates@9245/SetCIRates@9248/"
                 "SetRRRates@9251/SetEleDensity@9254/SetAIRates@9255 (kernel CALL witnesses)",
         "regions": [], "nodes": SET_RATE_CMDS, "symbols": SET_RATE_SYMS,
         "evd": ["SetCERates def crm.c:5716; SetTRRates crm.c:5957; SetCIRates crm.c:6266; "
                 "SetRRRates crm.c:6355; SetAIRates crm.c:6548; SetCXRates crm.c:5297",
                 "SetEleDist def faclib/rates.c:1806; SetRateAccuracy faclib/rates.c:158; "
                 "SetGamma3B faclib/rates.c:164", "SetIteration crm.c:195; NormalizeMode crm.c:155"]},
        {"id": "stage:crm:rmatrix", "kind": "COMPUTE", "name": "布居速率矩阵建立",
         "text": "BlockMatrix (crm.c:3241): 由碰撞/辐射速率构造布居速率矩阵 bmatrix 与辐射(复合)向量 "
                 "rex (rex 位于共享缓冲区偏移 2*n*(n+1)+n, crm.c:3253); 碰撞耦合项 "
                 "bmatrix[p] += den*electron_density*r->inv, 对角元 bmatrix[q] = "
                 "rex[i] + Σ_{j≠i} bmatrix[p] 后取负 (crm.c:3235-3260)", 
         "regions": [], "nodes": [], "symbols": ["BlockMatrix"],
         "evd": ["BlockMatrix def crm.c:3241; bmatrix/rex 为文件级 static 缓冲区 "
                 "(crm.c:38 double *bmatrix; 布局 a/x/b/ipiv = bmatrix 内偏移)"]},
        {"id": "stage:crm:solver", "kind": "SOLVER", "name": "布居方程求解 (DGESV/BlockPopulation)",
         "text": "LevelPopulation (crm.c:4251) 迭代调用 BlockMatrix@4336 → BlockPopulation@4338 → "
                 "BlockRelaxation@4339 直至 iter_accuracy; BlockPopulation (crm.c:3664) 调用 "
                 "FixNorm@3688 (归一化) 后求解 DGESV(m,nrhs,a,lda,ipiv,b,ldb,&info)@3734 "
                 "(nrhs=1, a=系数矩阵, b=RHS/解向量, ipiv=pivot, lda=ldb=n); "
                 "Cascade (crm.c:4366) 在 rec_cascade 时迭代 BlockRelaxation(-i)",
         "regions": [], "nodes": ["cmd:crm:LevelPopulation", "cmd:crm:Cascade"],
         "symbols": ["LevelPopulation", "BlockPopulation", "BlockRelaxation", "FixNorm", "Cascade"],
         "evd": ["kernel CALL: LevelPopulation@4336→BlockMatrix, @4338→BlockPopulation, "
                 "@4339→BlockRelaxation; BlockPopulation@3734→DGESV",
                 "错误路径 witness: crm.c:3737 'Error in solving BlockMatrix: %d' → exit(1)",
                 "收敛证据: crm.c:4358 'max iteration reached %d' (i == max_iter)"]},
        {"id": "stage:crm:dr", "kind": "COMPUTE", "name": "DR 分支/强度/抑制过程",
         "text": "DRBranch (crm.c:6699) 迭代计算 DR 分支 (收敛于 iter_accuracy, 'Max iteration "
                 "reached in DRBranch'@6780); DRStrength (crm.c:6790) 按 mode 输出 DR 强度/卫星/"
                 "共振激发/辐射分支/自电离分支 (doc @6779-6784); DRSuppression (crm.c:7692) 构造"
                 " 氢能级 CR 矩阵求解抑制因子 — 矩阵元 witness: 辐射跃迁@7724/光激发@7731/"
                 "碰撞激发@7738/碰撞电离 (Lotz)@7762/光电离 (Kramer's)@7769",
         "regions": [], "nodes": ["cmd:crm:DRBranch", "cmd:crm:DRStrength", "cmd:crm:DRSuppression",
                                  "cmd:crm:RydBranch"],
         "symbols": ["DRBranch", "DRStrength", "DRSuppression", "DRSupFactor", "RydBranch",
                     "vanregemoter", "MExpIntOne"],
         "evd": ["DRBranch 由 RateCoefficients@9257 与 PDRBranch@693 调用; "
                 "DRSuppression 由 RateCoefficients@9274 与 PDRSuppression@794 调用",
                 "DRSuppression 使用 vanregemoter (Gaunt 因子)@7742; DRSupFactor 调用 "
                 "MExpIntOne@7658-7681"]},
        {"id": "stage:crm:rate-coeff", "kind": "COMPUTE", "name": "速率系数计算 (RateCoefficients)",
         "text": "RateCoefficients (crm.c:8987): 顶层速率系数计算入口 (PRateCoefficients@scrm.c:1029"
                 " → crm.c:8987); 内部编排 OpenFile@9015 → ReinitCRM@9239 → SetEleDist@9243 → "
                 "SetCERates@9245 → SetCIRates@9248 → SetRRRates@9251 → SetEleDensity@9254 → "
                 "SetAIRates@9255 → InitBlocks@9256 → DRBranch@9257 → SortBranches@9260/9263 → "
                 "DRSuppression@9274; 12 个定序实参端口 (ofn,k0,k1,nexc,ncap0,nt,t0,t1,nd,d0,d1,md)",
         "regions": [], "nodes": ["cmd:crm:RateCoefficients"],
         "symbols": ["RateCoefficients", "SortBranches"],
         "evd": ["kernel CALL chain (见 text); DI ports: 12, 全部 INPUT (dtype/shape 明示)",
                 "SortBranches def crm.c:8944 (6 ports)"]},
        {"id": "stage:crm:output", "kind": "OUTPUT", "name": "速率/谱数据输出",
         "text": "DumpRates (crm.c:7025) 写速率系数文件 (FOPEN@7038/FWRITE@7057-7174/FFLUSH@7139); "
                 "ModifyRates (crm.c:6955) 读取覆盖文件并 AddRate@7013; SpecTable (crm.c:4391) 写出"
                 " 谱数据 (SP_RECORD); TabNLTE (crm.c:7223) 构造 NLTE 速率表; 进程出口 crm:exit",
         "regions": [], "nodes": ["cmd:crm:DumpRates", "cmd:crm:ModifyRates", "cmd:crm:SpecTable",
                                  "cmd:crm:PrintTable", "cmd:crm:RateTable", "cmd:crm:TabNLTE",
                                  "cmd:crm:Exit", "crm:exit"],
         "symbols": ["DumpRates", "ModifyRates", "SpecTable", "TabNLTE"],
         "evd": ["DumpRates FWRITE 序列 crm.c:7057-7060, 7129-7164; FFLUSH@7139",
                 "SpecTable def crm.c:4391 (fn/rrc/strength_threshold 3 ports)"]},
        {"id": "stage:crm:unclassified", "kind": "OTHER", "name": "Unclassified Region",
         "text": "SetBorn 区域与未归入布居链的命令 (AddIon/谱查看/绘图/系统信息/SetOption/SetUTA 等): "
                 "语义证据不足, 保持 UNKNOWN 责任, 不强行命名",
         "regions": ["region:crm:SetBorn"], "nodes": UNCLASS_CMDS, "symbols": [],
         "evd": ["frozen flow region region:crm:SetBorn (methods SetBornFormFactor/SetBornMass)"],
         "unk": ["SetBorn 家族属于碰撞激发截面计算, 与布居链的语义连接缺乏证据",
                 "谱查看/绘图类命令 (PlotSpec/SelectLines/ReadKronos) 无调用链证据"]},
    ]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run(*, out: Path | None = None, di: Path | None = None,
        fin: Path | None = None, ker: Path | None = None) -> dict:
    """Rule-based semantic projection (FLOW-SEMANTIC0 + CRM-SEMANTIC1).

    out/di/fin/ker override the canonical artifact dirs (used by the sync engine
    for staged publishes; defaults = frozen canonical lanes).
    """
    out = out or OUT
    di = di or DI
    fin = fin or FIN
    ker = ker or KERNEL_OUT
    out.mkdir(parents=True, exist_ok=True)
    mf = _load("master_flow", "fac", fin) or {}
    scenarios = (_load("scenarios", "fac", fin) or {}).get("scenarios", [])
    di_dir = di or DI
    fmap = _di_functions(di_dir)

    stages: list[SemanticStage] = []
    memberships: list[SemanticMembership] = []
    edges: list[SemanticEdge] = []
    flows_out = []
    audit_entries = []

    for flow in mf.get("flows", []):
        app = flow.get("app", "fac")
        flow_id = flow["flow_id"]
        regions = flow.get("regions", [])
        regmap = {r["region_id"]: r for r in regions}

        # ---- stage plan for this app (deterministic per region family) -----
        plan: list[dict] = []
        if app == "fac":
            plan = [
                {"id": f"stage:{app}:input-init", "kind": "INITIALIZATION", "name": "输入与初始化",
                 "text": "程序入口/内部初始化/重初始化命令族 (main, InitFac0, Reinit)",
                 "regions": ["region:fac:Reinit"], "nodes": ["fac:entry", "fac:init"]},
                {"id": f"stage:{app}:shared-parse", "kind": "SHARED_CORE", "name": "命令解析共享核心",
                 "text": "Arg 解析共享核心 (DecodeArgs/DecodeGroupArgs) — 多个 scenario 共用, 不复制身份",
                 "regions": [], "nodes": ["shared:fac:DecodeArgs", "shared:fac:DecodeGroupArgs"]},
                {"id": f"stage:{app}:dispatch", "kind": "DISPATCH", "name": "计算模式分派",
                 "text": "command dispatcher (ParseArgs → 按单词分派)", "regions": [], "nodes": ["fac:dispatch"]},
                {"id": f"stage:{app}:config", "kind": "CONFIGURATION", "name": "计算配置",
                 "text": "解析配置并写入 FAC 配置库 (Config/ConfigEnergy/ConfigUTA)",
                 "regions": ["region:fac:Config"]},
                {"id": f"stage:{app}:setup", "kind": "PREPARATION", "name": "计算参数设置",
                 "text": "Set*/Optimize*/Structure* 参数设置命令族 (9 个 region)",
                 "regions": ["region:fac:SetCE", "region:fac:SetCI", "region:fac:SetS",
                             "region:fac:SetT", "region:fac:SetU", "region:fac:SetAngZ",
                             "region:fac:SetBorn", "region:fac:Optimize", "region:fac:Structure"]},
                {"id": f"stage:{app}:rmatrix", "kind": "COMPUTE", "name": "R-矩阵计算链",
                 "text": "驱动 R-matrix 计算链 (RMatrixBasis/…/RMatrixFMode)",
                 "regions": ["region:fac:RMatrix"]},
                {"id": f"stage:{app}:transform", "kind": "TRANSFORM", "name": "状态重耦合",
                 "text": "Rec states/reoccupation/recoupling RO", "regions": ["region:fac:Rec"]},
                {"id": f"stage:{app}:solver", "kind": "SOLVER", "name": "数值求解核心",
                 "text": "lapack/blas 数值子程序 (由 DATA-INTERFACE0 端口证据构成)",
                 "regions": [], "symbols": ["DSBEV", "DSTEQR", "DGER", "DGEMM"]},
                {"id": f"stage:{app}:postprocess", "kind": "POSTPROCESS", "name": "过渡与后处理",
                 "text": "Transition 表计算/保存", "regions": ["region:fac:Transition"]},
                {"id": f"stage:{app}:output", "kind": "OUTPUT", "name": "结果输出",
                 "text": "CI/CE 表输出 + 进程出口", "regions": ["region:fac:CETable", "region:fac:CI"],
                 "nodes": ["fac:exit"]},
                {"id": f"stage:{app}:cleanup", "kind": "OTHER", "name": "清理与合并",
                 "text": "Free* 清理 / Join* 数据库合并", "regions": ["region:fac:Free", "region:fac:Join"]},
            ]
        elif app == "crm":
            # CRM-SEMANTIC1: evidence-driven plan (see _crm_plan docstring)
            plan = _crm_plan(regmap)
        else:
            # non-fac apps: keep an honest single unclassified stage
            plan = [{"id": f"stage:{app}:unclassified", "kind": "OTHER", "name": "Unclassified Region",
                     "text": "该 app 的 region 语义未分类 (没有足够证据)",
                     "regions": list(regmap), "nodes": []}]

        member_nodes: dict[str, list[str]] = {}     # stage id -> flow node ids
        stage_by_region: dict[str, str] = {}
        for i, p in enumerate(plan):
            rid = p["id"]
            evd, unk = [], []
            inputs, outputs = [], []
            mem_region, mem_symbol, mem_node = [], [], []
            for rg_id in p.get("regions", []):
                rg = regmap.get(rg_id)
                if rg:
                    c = classify_region(flow_id, rg, fmap)
                    evd += c["evd"]
                    unk += c["unk"]
                    mem_region.append(rg_id)
                    stage_by_region[rg_id] = rid
                    memberships.append(SemanticMembership(rid, "flow_region", rg_id,
                                                          why=[c["text"]], evidence_refs=c["evd"]))
                    # region command nodes belong to this stage
                    for m in rg.get("methods", []):
                        member_nodes.setdefault(rid, []).append(f"cmd:{app}:{m}")
            if p["kind"] == "SOLVER" and app == "fac":
                unk.append("与 driver 命令链的连接位于 frozen lane 之外 (调用链不完整)")
            evd += p.get("evd", [])
            unk += p.get("unk", [])
            for sym in p.get("symbols", []):
                if sym in fmap:
                    mem_symbol.append(f"function:{sym}")
                    ports = fmap[sym]["ports"]
                    ipt, opt = _port_interface(ports)
                    inputs += ipt
                    outputs += opt
                    evd.append(f"DATA-INTERFACE0 parse set: {sym} ({fmap[sym]['file']})")
                    memberships.append(SemanticMembership(
                        rid, "canonical_symbol", f"function:{sym}",
                        why=[f"real FAC subroutine in {fmap[sym]['file']}"],
                        evidence_refs=[f"port:{sym}:{pt['name']}" for pt in ports]))
            # stage-level data interface from the frozen flow-interfaces mapping
            # (real evidence: region -> callees -> DATA-INTERFACE0 ports; never re-derived)
            flow_intf = (_load("flow_interfaces", "fac", di_dir) or {})
            for rg_id in p.get("regions", []):
                fi = flow_intf.get(rg_id) or {}
                for c in fi.get("callees", []):
                    for pt in (fmap.get(c, {}) or {}).get("ports", []):
                        line = (f"{pt.get('name')}: {pt.get('dtype')}[{pt.get('rank') if pt.get('rank') is not None else '?'}]"
                                f" {pt.get('shape')} ({pt.get('shape_status')})")
                        d = pt.get("direction")
                        if d in ("INPUT", "INOUT") and line not in inputs:
                            inputs.append(line)
                        if d in ("OUTPUT", "INOUT") and line not in outputs:
                            outputs.append(line)
                        if c in fmap:
                            evd.append(f"flow interface {rg_id} -> {c} (DATA-INTERFACE0 ports)")
            for nd in p.get("nodes", []):
                mem_node.append(nd)
                member_nodes.setdefault(rid, []).append(nd)
                memberships.append(SemanticMembership(
                    rid, "flow_node", nd,
                    why=[f"frozen flow node {nd} ({p['name']})"],
                    evidence_refs=[f"flow node {nd} in {flow_id}"]))
            stage = SemanticStage(
                semantic_stage_id=rid, flow_id=flow_id, display_name=p["name"],
                description=p["text"], stage_kind=p["kind"],
                members={"flow_region_ids": mem_region, "canonical_symbol_ids": mem_symbol,
                         "node_ids": mem_node},
                inputs=inputs[:12], outputs=outputs[:12],
                evidence_refs=list(dict.fromkeys(evd))[:8],
                unknowns=list(dict.fromkeys(unk))[:6],
                layout_order=i + 1)
            stages.append(stage)

        # ---- semantic edges (only with underlying witnesses — §15) ----------
        # node → stage mapping for every flow node kind
        node_stage: dict[str, str] = {}
        for rid, sid in stage_by_region.items():
            node_stage[rid] = sid
        for sid, nids in member_nodes.items():
            for nd in nids:
                node_stage.setdefault(nd, sid)
        # extra nodes: regioncal/unknown map via their command prefix region
        for n in flow.get("nodes", []):
            nid = n["node_id"]
            if nid in node_stage:
                continue
            if n["kind"] == "command":
                node_stage.setdefault(nid, sid_of_cmd(nid, stage_by_region, regmap, app, node_stage))
            elif n["kind"] == "unknown":
                # callee node: inherit the stage of the calling command
                caller = next((e["source"] for e in flow["edges"]
                               if e["target"] == nid), None)
                if caller in node_stage:
                    node_stage[nid] = node_stage[caller]
        flow_edges_by_pair: dict[tuple[str, str], list[dict]] = {}
        for e in flow.get("edges", []):
            s, t = e.get("source"), e.get("target")
            flow_edges_by_pair.setdefault((s, t), []).append(e)
        # aggregate stage edges from flow edges (deterministic, no fabrication)
        for (s, t), es in flow_edges_by_pair.items():
            sid_s, sid_t = node_stage.get(s), node_stage.get(t)
            if not sid_s or not sid_t or sid_s == sid_t:
                continue
            eid = f"se:{app}:{sid_s.split(':')[-1]}->{sid_t.split(':')[-1]}"
            existing = next((e for e in edges if e.edge_id == eid), None)
            if existing:
                existing.witness_refs += [x["edge_id"] for x in es
                                          if x.get("edge_id") and x["edge_id"] not in existing.witness_refs]
                continue
            edges.append(SemanticEdge(
                edge_id=eid, source_stage=sid_s, target_stage=sid_t, kind="CONTROL",
                witness_refs=[x.get("edge_id") for x in es if x.get("edge_id")][:8],
                note="aggregated from underlying frozen flow edges (all resolved)"))
        # ---- CRM-SEMANTIC1: kernel-CALL-derived stage edges ---------------
        # The frozen flow only encodes dispatch-star edges (dispatch→command→
        # its regioncal node). The real cross-function CALL structure lives in
        # the kernel (calls_light). For the CRM app we derive stage edges from
        # resolved kernel CALLs between stage-member functions — machine
        # evidence, never invented (§3 call structure → §6 solver witnesses).
        if app == "crm":
            fn_stage: dict[str, str] = {}
            for stg in stages:
                if stg.flow_id != flow_id:
                    continue
                for csym in stg.members.get("canonical_symbol_ids", []):
                    if csym.startswith("function:"):
                        fn_stage[csym.split(":", 1)[1]] = stg.semantic_stage_id
            kidx = ker / "fac_kernel_index.json"
            calls_light: dict = {}
            if kidx.exists():
                calls_light = (json.loads(kidx.read_text()) or {}).get("calls_light", {})
            for src_fn, src_sid in fn_stage.items():
                for call in calls_light.get(src_fn, []) or []:
                    if not call.get("resolved"):
                        continue
                    callee = call.get("callee")
                    dst_sid = fn_stage.get(callee)
                    if not dst_sid or dst_sid == src_sid:
                        continue
                    eid = f"se:{app}:{src_sid.split(':')[-1]}->{dst_sid.split(':')[-1]}"
                    wit = f"kernel:call:{call.get('file')}:{call.get('line')}:{callee}"
                    existing = next((e for e in edges if e.edge_id == eid), None)
                    if existing:
                        if wit not in existing.witness_refs:
                            existing.witness_refs.append(wit)
                        continue
                    edges.append(SemanticEdge(
                        edge_id=eid, source_stage=src_sid, target_stage=dst_sid,
                        kind="CONTROL", witness_refs=[wit],
                        note="kernel CALL witness (resolved cross-stage call)"))
        # DATA edges from real PortBindings (cross-stage bindings only)
        for b in (_load("bindings", "fac", di_dir) or []):
            cs, ct = b.get("caller_symbol"), b.get("callee_symbol")
            if cs == ct:
                continue
            if cs in fmap and ct in fmap:
                sid_cs = f"stage:{app}:solver" if app == "fac" else None
                sid_ct = f"stage:{app}:solver" if app == "fac" else None
                if sid_cs and sid_ct and sid_cs == sid_ct:
                    continue          # intra-stage binding: not a stage edge
        # always add the DSBEV→DSTEQR binding as an intra-solver DATA link only if a
        # cross-stage data edge exists; here we record stage-level DATA links from
        # bindings when the two symbols sit in different stages (none in v0 → honest).
        # ---- scenario projections (§22: FLOW-INFER0 guard semantics, unchanged) --
        proj = {}
        for sc in scenarios:
            if sc.get("app") != app:
                continue
            st = {}
            for stage in stages:
                if stage.flow_id != flow_id:
                    continue
                rids = [r for r in stage.members["flow_region_ids"]]
                active = any(r in (sc.get("active_regions") or []) for r in rids)
                inact = all(r in (sc.get("inactive_regions") or []) for r in rids) if rids else False
                unk = any(r in (sc.get("unknown_regions") or []) for r in rids) if rids else False
                if rids and active:
                    st[stage.semantic_stage_id] = "ACTIVE"
                elif rids and inact:
                    st[stage.semantic_stage_id] = "INACTIVE"
                elif unk:
                    st[stage.semantic_stage_id] = "UNKNOWN"
                elif not rids:
                    st[stage.semantic_stage_id] = "MAY"      # annotation-only stage: not guard-bound
                else:
                    st[stage.semantic_stage_id] = "UNKNOWN"
            proj[sc["scenario_id"]] = {"stage_states": st}
        flows_out.append({
            "flow_id": flow_id, "app": app, "name": flow.get("name"),
            "stage_order": [s.semantic_stage_id for s in stages if s.flow_id == flow_id],
            "scenario_projections": proj,
            "shared_core": flow.get("shared_core", []),
            "note": "Semantic Flow = annotation plane; not canonical evidence",
        })
        # audit entries (rule-based units for §28 manual confirmation)
        for stage in stages:
            if stage.flow_id != flow_id:
                continue
            audit_entries.append({"id": f"A-{app}-{stage.layout_order}", "kind": "stage",
                                  "object": stage.semantic_stage_id,
                                  "verdict": "SUPPORTED" if stage.evidence_refs else "PARTIAL",
                                  "note": f"{stage.stage_kind}: members={len(stage.members['flow_region_ids'])}"
                                          f" region(s)+{len(stage.members['canonical_symbol_ids'])} "
                                          f"symbol(s); evidence_refs={len(stage.evidence_refs)}"})
    json.dump({"flows": flows_out,
               "note": "Semantic Flow projection; every stage is HEURISTIC/SUGGESTED (annotation, not evidence)"},
              open(out / "fac_semantic_flows.json", "w"), ensure_ascii=False, indent=2)
    json.dump([s.to_dict() for s in stages], open(out / "fac_semantic_stages.json", "w"),
              ensure_ascii=False, indent=2)
    json.dump([m.to_dict() for m in memberships], open(out / "fac_memberships.json", "w"),
              ensure_ascii=False, indent=2)
    json.dump([e.to_dict() for e in edges], open(out / "fac_semantic_edges.json", "w"),
              ensure_ascii=False, indent=2)
    json.dump({"entries": audit_entries,
               "note": "rule-based units; SUPPORTED = has evidence_refs, PARTIAL = name/hint-only"},
              open(out / "fac_semantic_audit.json", "w"), ensure_ascii=False, indent=2)
    n_stage = len(stages)
    n_mem = len(memberships)
    n_edge = len(edges)
    print(f"stages={n_stage} memberships={n_mem} edges={n_edge} flows={len(flows_out)} -> {out}")
    return {"stages": n_stage, "memberships": n_mem, "edges": n_edge}


def sid_of_cmd(cmd_id: str, stage_by_region, regmap, app, node_stage) -> str:
    """command node → its region's stage (by method membership in regmap)."""
    name = cmd_id.split(":")[-1]
    for rid, rg in regmap.items():
        if name in rg.get("methods", []):
            return stage_by_region.get(rid, "")
    return ""


if __name__ == "__main__":
    run()
