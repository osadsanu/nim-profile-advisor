"""NIM profile recommendation engine.

Given a GPU model (any alias, k8s label, or PCI device id) and the number of GPUs,
every profile of every NIM is checked for compatibility and the best profile per
NIM is chosen. The checks mirror what NIM itself verifies at start-up:

* hardware-specific profiles  - gpu_device PCI id must match the GPU
                                (or internal gpu name / compute capability for non-LLM NIMs)
* generic profiles            - precision must be supported by the GPU architecture
* resources                   - tp x pp (or n_gpus) per node, nodes, min VRAM per GPU
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .normalize import norm_name

# Minimum compute capability needed to run a precision on a *generic* profile.
# bf16 needs Ampere (8.0); FP8 tensor cores need Ada/Hopper (8.9/9.0);
# NVFP4 needs Blackwell (10.x / 12.x).
PRECISION_MIN_CC: dict[str, float] = {
    "fp32": 0.0, "fp16": 0.0,
    "int8": 7.5, "w8a8": 7.5, "int4": 7.5, "int4_awq": 7.5, "awq": 7.5, "w4a16": 7.5, "gptq": 7.5,
    "bf16": 8.0, "mxfp4": 8.0,
    "fp8": 8.9,
    "nvfp4": 10.0, "fp4": 10.0,
}

# Approximate bytes per parameter, used to estimate weight memory for generic LLM profiles
BYTES_PER_PARAM: dict[str, float] = {
    "fp32": 4.0, "fp16": 2.0, "bf16": 2.0, "fp8": 1.0, "int8": 1.0, "w8a8": 1.0,
    "nvfp4": 0.5625, "fp4": 0.5625, "mxfp4": 0.53, "int4": 0.5625, "int4_awq": 0.5625, "awq": 0.5625,
    "w4a16": 0.5625, "gptq": 0.5625,
}

# Higher = faster / more efficient
PRECISION_SPEED: dict[str, int] = {
    "nvfp4": 6, "fp4": 6, "mxfp4": 5, "fp8": 5, "int8": 4, "w8a8": 4,
    "int4_awq": 3, "awq": 3, "w4a16": 3, "int4": 3, "gptq": 3,
    "bf16": 2, "fp16": 2, "fp32": 1,
}
# Higher = closer to the original model weights
PRECISION_QUALITY: dict[str, int] = {
    "fp32": 6, "bf16": 5, "fp16": 5, "fp8": 4, "int8": 3, "w8a8": 3, "mxfp4": 2, "nvfp4": 2, "fp4": 2,
    "int4_awq": 1, "awq": 1, "w4a16": 1, "int4": 1, "gptq": 1,
}

OPTIMIZED_ENGINES = {"tensorrt_llm", "tensorrt", "prebuilt"}
# "fit" shown in the UI (3 = exact PCI device match)
MATCH_RANK = {"gpu_device": 3, "gpu_name": 2, "compute_capability": 2, "generic": 1}
# ranking order: a listed GPU name beats an engine accepted only by compute capability
MATCH_ORDER = {"gpu_device": 4, "gpu_name": 3, "compute_capability": 2, "generic": 1}
MATCH_LABEL = {
    "gpu_device": "Exact GPU match (PCI device id)",
    "gpu_name": "GPU-specific (internal GPU name)",
    "compute_capability": "Compute-capability specific engine",
    "generic": "Generic profile (any supported GPU)",
}
GOALS = {
    "nim": "NIM automatic selection (default)",
    "throughput": "Maximum throughput",
    "latency": "Lowest latency",
    "quality": "Highest precision / quality",
    "min_gpus": "Fewest GPUs per replica",
}

MIG_MEM = re.compile(r"(\d+)g\.(\d+)gb", re.I)
PARAMS = re.compile(r"(?<![a-z0-9])(?:(\d+)x)?(\d+(?:\.\d+)?)b(?![a-z0-9])")
PCAI_SIZES = {"Developer": 0, "Small": 1, "Medium": 2, "Large": 3, "Expansion rack": 4}


def _pcai_order(c: dict) -> tuple:
    """Current (G2) systems first, then by T-shirt size."""
    return (c.get("status") != "current", PCAI_SIZES.get(c.get("size"), 9), c.get("gpu_id") or "", c["_id"])


def _cc(v: Any) -> float | None:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# In-memory snapshot of the database (reloaded when a new import run appears)
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._run_id: str | None = None
        self._checked = 0.0
        self.gpus: dict[str, dict] = {}
        self.models: dict[str, dict] = {}
        self.profiles: dict[str, list[dict]] = {}
        self.pcai: list[dict] = []
        self.validation: dict[str, dict] = {}

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._run_id is not None and now - self._checked < 10:
            return
        self._checked = now
        run = self.db.import_runs.find_one(sort=[("started_at", -1)], projection={"_id": 1})
        run_id = run["_id"] if run else "none"
        if not force and run_id == self._run_id:
            return
        with self._lock:
            gpus = {g["_id"]: g for g in self.db.gpus.find()}
            models = {m["_id"]: m for m in self.db.models.find({}, {"manifest_yaml": 0})}
            profiles: dict[str, list[dict]] = defaultdict(list)
            for p in self.db.profiles.find({}, {"workspace": 0}):
                profiles[p["model_id"]].append(p)
            self.gpus, self.models, self.profiles = gpus, models, dict(profiles)
            self.pcai = sorted(self.db.pcai_configurations.find(), key=_pcai_order)
            self.validation = {v["_id"]: v for v in self.db.gpu_device_validation.find()}
            self._run_id = run_id

    @property
    def run_id(self) -> str | None:
        return self._run_id


# ---------------------------------------------------------------------------
# GPU resolution
# ---------------------------------------------------------------------------

def resolve_gpu(store: Store, query: str) -> list[dict]:
    """Resolve a GPU id, alias, k8s label, nvidia-smi name or PCI id to GPU documents."""
    q = (query or "").strip()
    if not q:
        return []
    if q in store.gpus:
        return [store.gpus[q]]
    dev = re.fullmatch(r"(?:0x)?([0-9a-fA-F]{4})(?::(?:0x)?10de)?", q)
    if dev:
        hits = [g for g in store.gpus.values() if dev.group(1).lower() in g["pci_device_ids"]]
        if hits:
            return hits
    n = norm_name(q)
    if not n:
        return []
    # family names ('H200', 'RTX PRO 6000', 'GH200') are ambiguous: let the user pick the SKU
    family = [g for g in store.gpus.values() if g.get("family") and norm_name(g["family"]) == n]
    if len(family) > 1:
        family.sort(key=lambda g: (not g.get("in_pcai"), g["name"]))
        return family
    exact = [g for g in store.gpus.values() if n in g["aliases_norm"]]
    if exact:
        return exact
    partial = [g for g in store.gpus.values() if any(n in a for a in g["aliases_norm"])]
    partial.sort(key=lambda g: (not g.get("in_pcai"), g["name"]))
    return partial


def gpu_summary(store: Store, g: dict) -> dict:
    pcai = [{"id": c["_id"], "name": c["name"], "size": c["size"], "gpus_per_worker_node": c["gpus_per_worker_node"]}
            for c in store.pcai if c["gpu_id"] == g["_id"]]
    validation = []
    for d in g["pci_device_ids"]:
        v = store.validation.get(f"device:{d}")
        validation.append({
            "gpu_device": f"{d}:10de",
            "status": v["status"] if v else "not used by any NIM profile",
            "profiles": v["profile_count"] if v else 0,
            "models": v["model_count"] if v else 0,
            "internal_names": [c["name"] for c in (v or {}).get("cooccurrence", [])],
        })
    return {
        "id": g["_id"], "name": g["name"], "family": g.get("family"), "chip": g.get("chip"),
        "architecture": g["architecture"],
        "compute_capability": g["compute_capability"], "vram_gb": g.get("vram_gb"),
        "form_factor": g.get("form_factor"), "pci_device_ids": g["pci_device_ids"],
        "nim_gpu_device": g.get("nim_gpu_device", []), "aliases": g.get("aliases", []),
        "nvidia_smi_names": g.get("nvidia_smi_names", []),
        "internal_names": g.get("internal_names", []), "k8s_gpu_product": g.get("k8s_gpu_product", []),
        "fp8": g.get("fp8"), "nvfp4": g.get("nvfp4"), "confidence": g.get("confidence"),
        "in_pcai": g.get("in_pcai", False), "pcai_configurations": pcai, "validation": validation,
        "sources": g.get("sources", []),
    }


# ---------------------------------------------------------------------------
# Profile evaluation
# ---------------------------------------------------------------------------

@dataclass
class Context:
    gpu: dict
    count: int
    nodes: int
    vram_gb: float | None
    mig: str | None
    goal: str
    lora: bool

    @property
    def cc(self) -> float | None:
        return _cc(self.gpu.get("compute_capability"))


def model_params_b(model: dict) -> float | None:
    text = " ".join([str((model.get("manifest") or {}).get("model") or ""), model.get("name", ""),
                     model.get("_id", "")]).lower()
    best = None
    for mult, num in PARAMS.findall(text):
        value = float(num) * (int(mult) if mult else 1)
        if value >= 0.1:
            best = max(best or 0.0, value)
    return best


def _gpu_names(store: Store, ids: list[str]) -> str:
    names = [store.gpus[i]["name"] for i in ids if i in store.gpus]
    return ", ".join(names)


def _same_family(store: Store, ids: list[str], gpu: dict) -> bool:
    fam = gpu.get("family")
    return bool(fam) and any(store.gpus.get(i, {}).get("family") == fam for i in ids)


def evaluate(store: Store, model: dict, p: dict, ctx: Context) -> dict:
    hw = p["hw"]
    mode = hw["match_mode"]
    match = mode
    gpu = ctx.gpu
    blockers: list[str] = []
    warnings: list[str] = []
    # why a blocked profile could still be forced with NIM_MODEL_PROFILE:
    #   "family" - built for a sibling SKU of the same family (e.g. H200 SXM vs H200 NVL)
    #   "name"   - the manifest names this GPU but carries another gpu_device (manifest error, e.g. L40S + 26b5)
    sibling: str | None = None

    if mode == "gpu_device":
        if not set(hw["gpu_device_ids"]) & set(gpu["pci_device_ids"]):
            target = _gpu_names(store, hw["gpu_ids"]) or ", ".join(f"{d}:10de" for d in hw["gpu_device_ids"])
            devices = ", ".join(hw["gpu_device_ids"])
            if gpu["_id"] in (hw.get("name_gpu_ids") or []):
                blockers.append(f"manifest error: tagged gpu '{', '.join(hw['gpu_names'])}' but gpu_device {devices} "
                                f"is {target}, so NIM does not auto-select it")
                sibling = "name"
            else:
                blockers.append(f"optimized for {target} (gpu_device {devices})")
                if _same_family(store, hw["gpu_ids"], gpu):
                    sibling = "family"
    elif mode == "gpu_name":
        if gpu["_id"] not in hw["gpu_ids"]:
            listed = _gpu_names(store, hw["gpu_ids"]) or ", ".join(hw["gpu_names"])
            cc = hw["compute_capability"]
            if cc and ctx.cc is not None and _cc(cc) == ctx.cc:
                # TensorRT engines are SM specific: same compute capability normally works
                match = "compute_capability"
                warnings.append(f"engine built for compute capability {cc} and tested on {listed}; "
                                f"{gpu['name']} has the same compute capability")
            else:
                blockers.append(f"built for {listed}" + (f" (compute capability {cc})" if cc else ""))
                if _same_family(store, hw["gpu_ids"], gpu):
                    sibling = "family"
    elif mode == "compute_capability":
        if _cc(hw["compute_capability"]) != ctx.cc:
            blockers.append(f"engine built for compute capability {hw['compute_capability']} "
                            f"(this GPU is {gpu['compute_capability']})")
    else:
        need = PRECISION_MIN_CC.get(p.get("precision") or "")
        if need is not None and ctx.cc is not None and ctx.cc + 1e-9 < need:
            blockers.append(f"{p['precision']} needs compute capability >= {need} "
                            f"({gpu['name']} is {gpu['compute_capability']})")

    if p["nodes_required"] > ctx.nodes:
        blockers.append(f"multi-node profile: needs {p['nodes_required']} nodes")
    if p["gpus_per_node_required"] > ctx.count:
        blockers.append(f"needs {p['gpus_per_node_required']} GPUs per node (tp={p['tp']}, pp={p['pp']})")
    if ctx.mig and p["gpus_required"] > 1:
        blockers.append("MIG slices cannot be combined for multi-GPU (tp/pp) profiles")

    vram_need = p.get("min_vram_gb")
    estimated = None
    if vram_need is None and p.get("is_llm") and mode == "generic":
        params = model_params_b(model)
        bpp = BYTES_PER_PARAM.get(p.get("precision") or "", 2.0)
        if params:
            estimated = round(params * bpp * 1.1 / max(p["gpus_required"], 1), 1)
    if ctx.vram_gb:
        if vram_need is not None and vram_need > ctx.vram_gb + 0.5:
            blockers.append(f"needs >= {vram_need:g} GB per GPU ({ctx.vram_gb:g} GB available)")
        elif estimated is not None and estimated > ctx.vram_gb * 0.92:
            blockers.append(f"estimated weights ~{estimated:g} GB per GPU do not fit in {ctx.vram_gb:g} GB "
                            f"(tp={p['tp']}, {p.get('precision') or 'bf16'})")
        elif estimated is not None and estimated > ctx.vram_gb * 0.75:
            warnings.append(f"tight memory: ~{estimated:g} GB of weights per GPU leaves little KV-cache room")
    if mode == "generic" and p.get("is_llm") and vram_need is None and estimated is None:
        warnings.append("memory fit not verified (no min_vram tag and model size unknown)")
    if p.get("feat_lora") and not ctx.lora:
        warnings.append("LoRA-enabled profile (needs NIM_PEFT_SOURCE to use adapters)")
    if hw.get("consistency") == "conflict":
        warnings.append("manifest gpu name and gpu_device disagree for this profile")
    if ctx.mig:
        warnings.append("MIG: check that this NIM version supports MIG instances")

    forceable = bool(sibling) and len(blockers) == 1
    return {
        "compatible": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        # blocked only by the hardware tag and otherwise fits: NIM will not auto-select it,
        # but it can be forced with NIM_MODEL_PROFILE at your own risk
        "sibling": forceable,
        "sibling_reason": sibling if forceable else None,
        "match": match,
        "estimated_vram_gb": estimated,
    }


def rank_key(p: dict, ev: dict, ctx: Context) -> tuple:
    precision = p.get("precision") or ""
    hw_rank = MATCH_ORDER[ev["match"]]
    optimized = 1 if (p.get("nim_optimized") or (p.get("engine") in OPTIMIZED_ENGINES and ev["match"] != "generic")) else 0
    speed = PRECISION_SPEED.get(precision, 1)
    quality = PRECISION_QUALITY.get(precision, 1)
    lora_ok = 1 if (bool(p.get("feat_lora")) == ctx.lora or p.get("feat_lora") is None) else 0
    ptype = p.get("profile_type")
    gpus = p["gpus_required"]
    no_warn = 0 if ev["warnings"] else 1

    if ctx.goal == "throughput":
        return (hw_rank, optimized, lora_ok, ptype == "throughput", speed, -gpus, no_warn)
    if ctx.goal == "latency":
        return (hw_rank, optimized, lora_ok, ptype == "latency", gpus, speed, no_warn)
    if ctx.goal == "quality":
        return (lora_ok, quality, hw_rank, optimized, ptype == "throughput", -gpus, no_warn)
    if ctx.goal == "min_gpus":
        return (-gpus, hw_rank, optimized, lora_ok, speed, no_warn)
    # "nim": emulate NIM automatic selection - most specific hardware match, optimized engine,
    # most efficient precision, non-LoRA, then use the GPUs that were given (higher tp)
    return (hw_rank, optimized, lora_ok, speed, ptype == "throughput", gpus, no_warn)


def explain(p: dict, ev: dict, ctx: Context) -> list[str]:
    why = [MATCH_LABEL[ev["match"]]]
    if p.get("nim_optimized"):
        why.append("NVIDIA-optimized profile (nim_optimized)")
    elif p.get("engine") in OPTIMIZED_ENGINES and ev["match"] != "generic":
        why.append(f"prebuilt {p['engine']} engine for this GPU")
    if p.get("precision"):
        why.append(f"precision {p['precision']}")
    if p.get("profile_type"):
        why.append(f"{p['profile_type']}-optimized")
    why.append(f"uses {p['gpus_required']} GPU(s): tp={p['tp']} pp={p['pp']}")
    if ctx.count >= p["gpus_per_node_required"] > 0 and ctx.nodes == 1:
        replicas = ctx.count // p["gpus_per_node_required"]
        if replicas > 1:
            why.append(f"{replicas} replicas fit on {ctx.count} GPUs")
    return why


def profile_view(p: dict, ev: dict | None = None) -> dict:
    out = {
        "profile_id": p["profile_id"], "precision": p.get("precision"), "engine": p.get("engine"),
        "tp": p["tp"], "pp": p["pp"], "gpus_required": p["gpus_required"], "nodes_required": p["nodes_required"],
        "profile_type": p.get("profile_type"), "profile_tag": p.get("profile_tag"),
        "nim_optimized": p.get("nim_optimized"), "feat_lora": p.get("feat_lora"), "min_vram_gb": p.get("min_vram_gb"),
        "nim_description": p.get("nim_description"), "match_mode": p["hw"]["match_mode"],
        "gpu_device_ids": p["hw"]["gpu_device_ids"], "gpu_names": p["hw"]["gpu_names"],
        "compute_capability": p["hw"]["compute_capability"], "gpu_ids": p["hw"]["gpu_ids"],
        "tags": p.get("tags", {}),
    }
    if ev:
        out.update({"compatible": ev["compatible"], "blockers": ev["blockers"], "warnings": ev["warnings"],
                    "estimated_vram_gb": ev["estimated_vram_gb"], "sibling": ev["sibling"],
                    "sibling_reason": ev["sibling_reason"], "match": ev["match"]})
    return out


def deploy_snippet(model: dict, p: dict) -> dict:
    image = model["image"]
    gpus = p["gpus_required"]
    return {
        "env": {"NIM_MODEL_PROFILE": p["profile_id"]},
        "kubernetes": (
            f"env:\n  - name: NIM_MODEL_PROFILE\n    value: \"{p['profile_id']}\"\n"
            f"resources:\n  limits:\n    nvidia.com/gpu: {gpus}"
        ),
        "docker": (
            f"docker run --rm --gpus {gpus if gpus > 1 else 1} -e NGC_API_KEY \\\n"
            f"  -e NIM_MODEL_PROFILE={p['profile_id']} \\\n  -p 8000:8000 {image}"
        ),
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def recommend(store: Store, gpu_query: str, count: int, nodes: int = 1, goal: str = "nim",
              category: str | None = None, q: str | None = None, mig: str | None = None,
              vram_gb: float | None = None, lora: bool = False) -> dict:
    store.refresh()
    candidates = resolve_gpu(store, gpu_query)
    if not candidates:
        return {"error": f"Unknown GPU '{gpu_query}'", "gpu_candidates": []}
    if len(candidates) > 1 and gpu_query not in store.gpus:
        return {"error": f"'{gpu_query}' matches several GPUs, pick one",
                "gpu_candidates": [gpu_summary(store, g) for g in candidates[:12]]}
    gpu = candidates[0]

    count = max(1, int(count))
    nodes = max(1, int(nodes))
    vram = vram_gb or gpu.get("vram_gb")
    if mig:
        m = MIG_MEM.search(mig)
        if m:
            vram = float(m.group(2))
    goal = goal if goal in GOALS else "nim"
    ctx = Context(gpu=gpu, count=count, nodes=nodes, vram_gb=vram, mig=mig or None, goal=goal, lora=lora)

    needle = (q or "").strip().lower()
    results = []
    blocker_totals: Counter = Counter()
    for model in store.models.values():
        if category and model["category"] != category:
            continue
        if needle and needle not in " ".join([model["_id"], model["name"], model["display_name"],
                                               model.get("description") or ""]).lower():
            continue
        profiles = store.profiles.get(model["_id"], [])
        evaluated = [(p, evaluate(store, model, p, ctx)) for p in profiles]
        ok = [(p, ev) for p, ev in evaluated if ev["compatible"]]
        ok.sort(key=lambda pe: rank_key(pe[0], pe[1], ctx), reverse=True)
        reasons = Counter(b.split(" (")[0] for _, ev in evaluated if not ev["compatible"] for b in ev["blockers"])
        siblings = [profile_view(p, ev) for p, ev in sorted(
            ((p, ev) for p, ev in evaluated if ev["sibling"]),
            key=lambda pe: (pe[1]["sibling_reason"] != "name", [-x if isinstance(x, (int, float)) else 0
                                                                 for x in rank_key(pe[0], pe[1], ctx)]))][:5]
        entry = {
            "model": {
                "id": model["_id"], "name": model["name"], "display_name": model["display_name"],
                "category": model["category"], "image": model["image"], "fetched_image": model["fetched_image"],
                "latest_version": model.get("latest_version"), "profile_count": model["profile_count"],
                "fetch_status": model["fetch"]["status"], "fetch_message": model["fetch"].get("message"),
                "logo": model.get("logo"), "is_llm": model.get("is_llm"),
            },
            "compatible_count": len(ok),
            "status": "compatible" if ok else ("no-profiles" if not profiles else "incompatible"),
            "best": None, "alternatives": [], "blockers": reasons.most_common(4), "siblings": siblings,
        }
        if ok:
            best_p, best_ev = ok[0]
            entry["best"] = {**profile_view(best_p, best_ev), "why": explain(best_p, best_ev, ctx),
                             "deploy": deploy_snippet(model, best_p)}
            entry["alternatives"] = [profile_view(p, ev) for p, ev in ok[1:6]]
            entry["fit"] = MATCH_RANK[best_ev["match"]]
        else:
            entry["fit"] = 0
            blocker_totals.update(r for r, _ in reasons.most_common(1))
        results.append(entry)

    results.sort(key=lambda e: (-e["fit"], e["status"] != "compatible", e["model"]["display_name"].lower()))
    summary = Counter(e["status"] for e in results)
    return {
        "gpu": gpu_summary(store, gpu),
        "request": {"gpu": gpu["_id"], "count": count, "nodes": nodes, "goal": goal, "goal_label": GOALS[goal],
                    "mig": mig, "vram_gb_per_device": vram, "category": category, "q": q, "lora": lora},
        "summary": {"models": len(results), "compatible": summary.get("compatible", 0),
                    "incompatible": summary.get("incompatible", 0), "no_profiles": summary.get("no-profiles", 0),
                    "exact_gpu_matches": sum(1 for e in results if e["fit"] == 3),
                    "top_blockers": blocker_totals.most_common(5)},
        "results": results,
    }


def model_detail(store: Store, model_id: str, gpu_query: str | None = None, count: int = 1, nodes: int = 1,
                 goal: str = "nim", mig: str | None = None, lora: bool = False,
                 vram_gb: float | None = None) -> dict | None:
    store.refresh()
    model = store.models.get(model_id)
    if not model:
        return None
    profiles = store.profiles.get(model_id, [])
    ctx = None
    if gpu_query:
        gpus = resolve_gpu(store, gpu_query)
        if len(gpus) == 1 or (gpus and gpu_query in store.gpus):
            g = gpus[0]
            vram = vram_gb or g.get("vram_gb")
            if mig and MIG_MEM.search(mig):
                vram = float(MIG_MEM.search(mig).group(2))
            ctx = Context(gpu=g, count=max(1, count), nodes=max(1, nodes), vram_gb=vram, mig=mig, goal=goal, lora=lora)
    rows = []
    for p in profiles:
        ev = evaluate(store, model, p, ctx) if ctx else None
        row = profile_view(p, ev)
        if ctx and ev and ev["compatible"]:
            row["rank"] = list(rank_key(p, ev, ctx))
        rows.append(row)
    if ctx:
        rows.sort(key=lambda r: (not r["compatible"], [-x if isinstance(x, (int, float)) else 0
                                                        for x in r.get("rank", [])]))
    return {"model": {k: v for k, v in model.items()}, "profiles": rows,
            "gpu": gpu_summary(store, ctx.gpu) if ctx else None}
