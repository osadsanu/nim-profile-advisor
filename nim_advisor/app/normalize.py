"""Parsing and normalization of NIM model manifests.

NIM manifests are heterogeneous: LLM NIMs use gpu_device/tp/pp/llm_engine,
speech/vision/bio NIMs use gpu names, compute_capability, backend, model_type,
n_gpus, vram, ...  This module turns every profile into one normalized shape
while keeping the raw tags untouched.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import yaml

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
DEVICE_ID = re.compile(r"^[0-9a-f]{4}$")


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

class _PermissiveLoader(yaml.SafeLoader):
    """SafeLoader that accepts custom tags such as !workspace or !name."""


def _construct_any(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_PermissiveLoader.add_multi_constructor("!", _construct_any)


def load_manifest(text: str) -> dict:
    data = yaml.load(text, Loader=_PermissiveLoader)  # noqa: S506 - SafeLoader subclass
    if not isinstance(data, dict):
        raise ValueError("manifest is not a YAML mapping")
    return data


def to_jsonable(value: Any) -> Any:
    """Make YAML values safe for MongoDB (string keys, no exotic types)."""
    if isinstance(value, dict):
        return {str(k).lstrip("$"): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def iter_profiles(manifest: dict) -> list[dict]:
    """Return the list of raw profiles regardless of manifest format.

    Format 1 (schema_version 2.x):  profiles: [ {id, tags, workspace}, ... ]
    Format 2 (older):               <64-hex id>: {model, release, tags, workspace}
    Format 3 (older Riva/BioNeMo):  model_profile: <id>
                                    manifest: [ {id, model, nim_name, release, tags, workspace}, ... ]
    Profile ids are not always 64-hex (e.g. 'gliner-pii-default', UUIDs, 'MolMIM').
    """
    profiles = manifest.get("profiles")
    if profiles is None and isinstance(manifest.get("manifest"), list):
        profiles = manifest["manifest"]
    out: list[dict] = []
    if isinstance(profiles, list):
        for p in profiles:
            if isinstance(p, dict) and p.get("id") is not None:
                out.append(p)
    elif isinstance(profiles, dict):
        for pid, p in profiles.items():
            if isinstance(p, dict):
                out.append({"id": pid, **p})
    else:
        for key, p in manifest.items():
            if HEX64.match(str(key)) and isinstance(p, dict):
                out.append({"id": key, **p})
    return out


def _str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v).strip()


def _int(v: Any) -> int | None:
    try:
        return int(float(_str(v)))
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(_str(v))
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool | None:
    s = _str(v).lower()
    if s in ("true", "1", "yes", "enabled"):
        return True
    if s in ("false", "0", "no", "disabled"):
        return False
    return None


def norm_name(name: str) -> str:
    """Normalize a GPU name for alias matching: 'NVIDIA H200-NVL' -> 'h200nvl'."""
    s = name.lower().replace("nvidia", "")
    return re.sub(r"[^a-z0-9]", "", s)


def parse_device_ids(raw: Any) -> tuple[list[str], list[str]]:
    """'2335:10de' / '2335' / '2335:10de,233b:10de' -> (['2335', ...], [invalid...])."""
    ids, bad = [], []
    for part in re.split(r"[,\s;]+", _str(raw)):
        if not part:
            continue
        dev = part.split(":")[0].lower().removeprefix("0x")
        if DEVICE_ID.match(dev):
            if dev not in ids:
                ids.append(dev)
        else:
            bad.append(part)
    return ids, bad


def parse_gpu_names(*raws: Any) -> list[str]:
    names = []
    for raw in raws:
        for part in re.split(r"[,;]+", _str(raw)):
            part = part.strip()
            if part and part not in names:
                names.append(part)
    return names


_SM = re.compile(r"^sm_?(\d+)(\d)[a-z]*$", re.I)


def parse_compute_capability(tags: dict) -> str | None:
    """compute_capability '8.9' / gpu_cc '8.9' / gpu_sm_version 'sm_100' -> '8.9' / '10.0'."""
    cc = tags.get("compute_capability") or tags.get("gpu_cc")
    if cc:
        return cc
    m = _SM.match(tags.get("gpu_sm_version") or "")
    return f"{int(m.group(1))}.{m.group(2)}" if m else None


# gpu tag values that mean "not tied to a GPU model"
GENERIC_GPU_NAMES = {"generic", "any", "fallback", "all", "none", ""}

PRECISION_ALIASES = {
    "bfloat16": "bf16", "float16": "fp16", "half": "fp16", "float32": "fp32", "float8": "fp8",
    "int4-awq": "int4_awq", "awq": "int4_awq",
}

ENGINE_ALIASES = {
    "tensorrt_llm": "tensorrt_llm", "tensorrt-llm": "tensorrt_llm", "trt-llm": "tensorrt_llm",
    "trtllm": "tensorrt_llm", "trt_llm": "tensorrt_llm",
    "vllm": "vllm", "vllm-omni": "vllm-omni", "sglang": "sglang",
    "trt": "tensorrt", "tensorrt": "tensorrt",
    "onnx": "onnx", "pytorch": "pytorch", "torch": "pytorch",
    "prebuilt": "prebuilt", "rmir": "rmir",
}


def _engine(tags: dict) -> str | None:
    for key in ("llm_engine", "backend", "model_type"):
        v = _str(tags.get(key)).lower()
        if v:
            return ENGINE_ALIASES.get(v, v)
    return None


_SIZE = re.compile(r"^\s*([0-9.]+)\s*([kmgt]i?b?)?\s*$", re.I)


def _vram_gb(tags: dict) -> float | None:
    v = _float(tags.get("min_vram_per_device_gb"))
    if v is not None:
        return v
    raw = _str(tags.get("vram"))
    m = _SIZE.match(raw) if raw else None
    if m:
        num = float(m.group(1))
        unit = (m.group(2) or "gb").lower()
        factor = {
            "kib": 1024 / 1e9, "kb": 1e-6, "k": 1e-6,
            "mib": 1024**2 / 1e9, "mb": 1e-3, "m": 1e-3,
            "gib": 1024**3 / 1e9, "gb": 1.0, "g": 1.0,
            "tib": 1024**4 / 1e9, "tb": 1e3, "t": 1e3,
        }.get(unit, 1.0)
        return round(num * factor, 1)
    return None


def workspace_entries(ws: Any) -> list[dict]:
    """Flatten a profile workspace into [{path, uri, checksum}] (both formats).

    Format 1:  workspace: {files: {<path>: {uri, checksum}}}
    Format 2:  workspace: !workspace {components: [{dst, src: {repo_id, files: [...]}}]}
    """
    out: list[dict] = []
    if not isinstance(ws, dict):
        return out
    files = ws.get("files")
    if isinstance(files, dict):
        for path, meta in files.items():
            meta = meta if isinstance(meta, dict) else {}
            out.append({"path": str(path), "uri": _str(meta.get("uri")) or None,
                        "checksum": _str(meta.get("checksum")) or None})
        return out
    for comp in ws.get("components") or []:
        comp = comp if isinstance(comp, dict) else {}
        src = comp.get("src") if isinstance(comp.get("src"), dict) else {}
        repo = _str(src.get("repo_id"))
        dst = _str(comp.get("dst"))
        names = src.get("files") or []
        if isinstance(names, dict):
            names = list(names)
        for name in names:
            name = _str(name if not isinstance(name, dict) else name.get("name"))
            out.append({"path": f"{dst}/{name}" if dst else name,
                        "uri": f"{repo}?file={name}" if repo else None, "checksum": None})
    return out


def normalize_profile(raw: dict) -> dict:
    """Normalize one raw manifest profile into the stored profile shape."""
    tags_raw = raw.get("tags") or {}
    tags = {str(k): _str(v) for k, v in tags_raw.items()} if isinstance(tags_raw, dict) else {}

    # a few NIMs use device_id / gpu_key instead of gpu_device / gpu
    device_ids, bad_ids = parse_device_ids(tags.get("gpu_device") or tags.get("device_id"))
    names = parse_gpu_names(tags.get("gpu"), tags.get("gpu_key"))
    generic_name = any(n.lower() in GENERIC_GPU_NAMES for n in names)
    names = [n for n in names if n.lower() not in GENERIC_GPU_NAMES]
    cc = parse_compute_capability(tags)

    if device_ids:
        match_mode = "gpu_device"
    elif names:
        match_mode = "gpu_name"
    elif cc:
        match_mode = "compute_capability"
    else:
        match_mode = "generic"

    tp = _int(tags.get("tp")) or 1
    pp = _int(tags.get("pp")) or 1
    explicit = _int(tags.get("n_gpus")) or _int(tags.get("number_of_gpus"))
    gpus_required = max(explicit or 0, tp * pp, 1)
    nodes_required = max(_int(tags.get("number_of_nodes")) or _int(tags.get("nnodes")) or 1, 1)
    gpus_required = max(gpus_required, nodes_required)
    gpus_per_node = math.ceil(gpus_required / nodes_required)

    profile_tag = tags.get("profile", "").lower()
    scenario = tags.get("inference_scenario", "").lower()
    profile_type = profile_tag if profile_tag in ("latency", "throughput") else (
        scenario if scenario in ("latency", "throughput") else None)

    json_args: Any = tags.get("nim_json_args")
    if json_args:
        try:
            json_args = json.loads(json_args)
        except ValueError:
            pass

    engine = _engine(tags)
    llm_engine = _str(tags.get("llm_engine")).lower()
    workspace = workspace_entries(raw.get("workspace"))

    precision = tags.get("precision", "").lower() or None
    precision = PRECISION_ALIASES.get(precision, precision)

    return {
        "profile_id": _str(raw.get("id")),
        "tags": tags,
        "hw": {
            "match_mode": match_mode,
            "gpu_device_raw": tags.get("gpu_device") or None,
            "gpu_device_ids": device_ids,
            "gpu_device_invalid": bad_ids,
            "gpu_names": names,
            "gpu_name_generic": generic_name,
            "compute_capability": cc,
            "gpu_ids": [],            # resolved canonical GPU ids (filled by importer)
            "unresolved": [],         # names / ids we could not map
        },
        "tp": tp,
        "pp": pp,
        "gpus_required": gpus_required,
        "nodes_required": nodes_required,
        "gpus_per_node_required": gpus_per_node,
        "precision": precision,
        "engine": engine,
        "is_llm": bool(llm_engine) or engine in ("tensorrt_llm", "vllm", "sglang"),
        "profile_type": profile_type,
        "profile_tag": tags.get("profile") or None,
        "nim_optimized": _bool(tags.get("nim_optimized")) is True,
        "trtllm_buildable": _bool(tags.get("trtllm_buildable")) is True,
        "feat_lora": _bool(tags.get("feat_lora")),
        "min_vram_gb": _vram_gb(tags),
        "nim_description": tags.get("nim_description") or None,
        "nim_json_args": json_args or None,
        "workspace_file_count": len(workspace),
        "workspace_hash": _str(raw.get("workspace_hash")) or tags.get("nim_workspace_hash_v1") or None,
        "workspace": workspace,
    }


# ---------------------------------------------------------------------------
# Folder naming (must stay identical to bulk_fetch_profiles.sh)
# ---------------------------------------------------------------------------

# Catalog entries that mirror nvcr.io in a private registry: <prefix>nvcr.io/... is fetched from nvcr.io and its
# folder gets the suffix "_<last segment of the prefix>" (default "_ezmeral-common").
INTERNAL_PREFIX = os.environ.get("INTERNAL_REGISTRY_PREFIX", "registry.example.com/ezmeral-common/").rstrip("/") + "/"
MIRROR_SUFFIX = "_" + INTERNAL_PREFIX.rstrip("/").rsplit("/", 1)[-1]


def folder_for_image(image: str) -> tuple[str, str]:
    """Return (folder_name, fetched_image) exactly like bulk_fetch_profiles.sh."""
    fetch_image, suffix = image, ""
    if image.startswith(INTERNAL_PREFIX):
        fetch_image, suffix = image[len(INTERNAL_PREFIX):], MIRROR_SUFFIX
    last = fetch_image.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = last.split(":", 1)
    else:
        repo, tag = last, "latest"
        fetch_image = f"{fetch_image}:latest"
    folder = re.sub(r"[^A-Za-z0-9._-]", "_", f"{repo}_{tag}{suffix}")
    return folder, fetch_image
