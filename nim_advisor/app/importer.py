"""Import nim_catalog.json + nims_catalog/<model>/model_manifest.yaml + reference data into MongoDB.

Usage (inside the container):  python -m app.importer
Environment:
    MONGO_URI       mongodb://user:pass@mongo:27017/?authSource=admin
    MONGO_DB        nim_advisor
    DATA_DIR        /data   (contains nim_catalog.json, nims_catalog/, reference/)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .normalize import (
    INTERNAL_PREFIX, folder_for_image, iter_profiles, load_manifest, norm_name, normalize_profile, to_jsonable,
)
from .schema import ensure_schema

GPU_LABEL = re.compile(
    r"(?i)\b(A2|A10G?|A16|A30|A40|A100|A800|L4|L20|L40S?|H20|H100|H200|H800|B40|B100|B200|B300|GB200|GB300|"
    r"GH200|GB10|RTX|T4|V100|DGX Spark|Thor|Orin)\b")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _read_status(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["folder"]: row for row in csv.DictReader(fh, delimiter="\t")}


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def categorize(entry: dict, is_llm: bool) -> str:
    labels = entry.get("labels") or {}
    text = " ".join([
        entry.get("name", ""), entry.get("displayName", ""), entry.get("description", ""),
        " ".join(labels.get("general", [])),
    ]).lower()
    def has(*words: str) -> bool:
        return any(w in text for w in words)
    if has("rerank"):
        return "Retrieval: reranking"
    if has("embed", "retriever", "retrieval"):
        return "Retrieval: embedding"
    if has("alphafold", "openfold", "boltz", "esm", "diffdock", "molmim", "genmol", "proteinmpnn", "rfdiffusion",
           "evo2", "msa-search", "protein", "molecul", "drug discovery", "chemistry", "alchemi", "biology",
           "genomics", "life sciences"):
        return "Biology & chemistry"
    if has("riva", "asr", "tts", "speech", "parakeet", "canary", "whisper", "magpie", "chatterbox", "audio",
           "voice", "noise removal", "bnr", "studio-voice", "transcri") and not is_llm:
        return "Speech & audio"
    if is_llm:
        return "LLM / VLM"
    if has("vision", "image", "video", "detection", "ocr", "pose", "cosmos", "flux", "diffusion", "segment",
           "depth", "gaze", "relighting", "audio2face", "avatar", "3d", "lipsync", "eye contact"):
        return "Vision, video & 3D"
    return "Other"


# ---------------------------------------------------------------------------
# reference data
# ---------------------------------------------------------------------------

def build_gpu_docs(ref: dict, pcai: dict) -> tuple[list[dict], dict[str, list[str]], dict[str, list[str]]]:
    pcai_gpu_ids = {c.get("gpu_id") for c in pcai.get("configurations", [])}
    docs, device_index, alias_index = [], defaultdict(list), defaultdict(list)
    for g in ref.get("gpus", []):
        gid = g["id"]
        ids = sorted({str(i).lower().split(":")[0] for i in g.get("pci_device_ids", [])})
        aliases = sorted({a for a in [g["name"], gid, *g.get("aliases", []), *g.get("k8s_gpu_product", []),
                                      *g.get("internal_names", [])] if a})
        doc = {
            "_id": gid,
            "name": g["name"],
            "family": g.get("family"),
            "chip": g.get("chip"),
            "architecture": g.get("architecture") or "unknown",
            "compute_capability": str(g.get("compute_capability") or "unknown"),
            "vram_gb": _num(g.get("vram_gb")),
            "form_factor": g.get("form_factor"),
            "pci_device_ids": ids,
            "nim_gpu_device": [f"{i}:10de" for i in ids],
            "aliases": aliases,
            "aliases_norm": sorted({norm_name(a) for a in aliases}),
            "nvidia_smi_names": g.get("nvidia_smi_names", []),
            "internal_names": sorted(set(g.get("internal_names", []))),
            "k8s_gpu_product": g.get("k8s_gpu_product", []),
            "fp8": g.get("fp8"),
            "nvfp4": g.get("nvfp4"),
            "nvlink": g.get("nvlink"),
            "in_pcai": gid in pcai_gpu_ids,
            "confidence": g.get("confidence", "medium"),
            "sources": g.get("sources", []),
            "notes": g.get("notes"),
        }
        docs.append(doc)
        for i in ids:
            device_index[i].append(gid)
        for a in doc["aliases_norm"]:
            if gid not in alias_index[a]:
                alias_index[a].append(gid)
    return docs, dict(device_index), dict(alias_index)


def build_pcai_docs(pcai: dict, gpu_ids: set[str], warnings: list[str]) -> list[dict]:
    docs = []
    for c in pcai.get("configurations", []):
        if c.get("gpu_id") not in gpu_ids:
            warnings.append(f"PCAI configuration {c.get('id')} references unknown GPU {c.get('gpu_id')}")
        doc = {k: v for k, v in c.items() if k != "id"}
        doc.update({
            "_id": c["id"],
            "name": c["name"],
            "size": c.get("size") or "",
            "gpu_id": c.get("gpu_id") or "",
            "gpus_per_worker_node": c.get("gpus_per_worker_node") or 0,
            "mig_profiles": c.get("mig_profiles", []),
            "sources": c.get("sources", []),
        })
        docs.append(doc)
    return docs


def build_issue_docs(research: dict) -> list[dict]:
    """Manifest problems found by tools/validate_gpu_ids.py (one row per gpu_device / gpu name pair)."""
    docs = []
    for i, issue in enumerate(research.get("manifest_issues", [])):
        key = f"{issue['kind']}:{issue.get('gpu_device') or '-'}:{issue.get('gpu_name')}"
        docs.append({"_id": key if key not in {d["_id"] for d in docs} else f"{key}#{i}", **issue})
    return docs


# ---------------------------------------------------------------------------
# validation evidence
# ---------------------------------------------------------------------------

def build_validation(profiles: list[dict], device_index: dict, alias_index: dict, gpu_cc: dict[str, list[str]],
                     research: dict) -> list[dict]:
    dev = defaultdict(lambda: {"profiles": 0, "models": set(), "names": Counter()})
    names = defaultdict(lambda: {"profiles": 0, "models": set(), "devices": Counter()})
    ccs = defaultdict(lambda: {"profiles": 0, "models": set()})
    for p in profiles:
        hw = p["hw"]
        for d in hw["gpu_device_ids"]:
            dev[d]["profiles"] += 1
            dev[d]["models"].add(p["model_id"])
            dev[d]["names"].update(hw["gpu_names"])
        for n in hw["gpu_names"]:
            names[n]["profiles"] += 1
            names[n]["models"].add(p["model_id"])
            names[n]["devices"].update(hw["gpu_device_ids"])
        if hw["compute_capability"]:
            ccs[hw["compute_capability"]]["profiles"] += 1
            ccs[hw["compute_capability"]]["models"].add(p["model_id"])

    research_dev = research.get("gpu_devices", {})
    research_names = research.get("gpu_names", {})
    out = []

    for d, s in sorted(dev.items()):
        ref = device_index.get(d, [])
        co, notes, conflict, unresolved = [], [], False, False
        for name, count in s["names"].most_common():
            resolved = alias_index.get(norm_name(name), [])
            agrees = bool(set(resolved) & set(ref)) if resolved and ref else None
            co.append({"name": name, "profiles": count, "resolved_gpu_ids": resolved, "agrees": agrees})
            if resolved and ref and not agrees:
                conflict = True
                notes.append(f"gpu tag '{name}' resolves to {resolved} but device {d} is {ref}")
            if not resolved:
                unresolved = True
        if not ref:
            status = "unknown"
            notes.append(f"device id {d} is not in the GPU reference")
        elif conflict:
            status = "conflict"
        elif co and not unresolved:
            status = "validated"
        else:
            status = "mapped"
            if not co:
                notes.append("no internal gpu name tag next to this device id; mapping relies on the PCI-id reference")
        out.append({
            "_id": f"device:{d}", "kind": "gpu_device", "value": f"{d}:10de", "status": status, "gpu_ids": ref,
            "profile_count": s["profiles"], "model_count": len(s["models"]), "cooccurrence": co,
            "research": research_dev.get(d), "notes": notes,
        })

    for n, s in sorted(names.items(), key=lambda kv: kv[0].lower()):
        resolved = alias_index.get(norm_name(n), [])
        co, notes, conflict = [], [], False
        for d, count in s["devices"].most_common():
            ref = device_index.get(d, [])
            agrees = bool(set(ref) & set(resolved)) if ref and resolved else None
            co.append({"device": f"{d}:10de", "profiles": count, "resolved_gpu_ids": ref, "agrees": agrees})
            if ref and resolved and not agrees:
                conflict = True
                notes.append(f"device {d} is {ref} but name '{n}' resolves to {resolved}")
        if not resolved:
            status = "unknown"
            notes.append("internal gpu name not found in the GPU reference aliases")
        elif conflict:
            status = "conflict"
        elif co:
            status = "validated"
        else:
            status = "mapped"
        out.append({
            "_id": f"name:{n}", "kind": "gpu_name", "value": n, "status": status, "gpu_ids": resolved,
            "profile_count": s["profiles"], "model_count": len(s["models"]), "cooccurrence": co,
            "research": research_names.get(n) or research_names.get(n.lower()), "notes": notes,
        })

    for c, s in sorted(ccs.items()):
        ref = gpu_cc.get(c, [])
        out.append({
            "_id": f"cc:{c}", "kind": "compute_capability", "value": c, "status": "mapped" if ref else "unknown",
            "gpu_ids": ref, "profile_count": s["profiles"], "model_count": len(s["models"]), "cooccurrence": [],
            "research": None, "notes": [] if ref else [f"no reference GPU with compute capability {c}"],
        })
    return out


# ---------------------------------------------------------------------------
# main import
# ---------------------------------------------------------------------------

def run_import(db, data_dir: Path) -> dict:
    started = datetime.now(timezone.utc)
    warnings: list[str] = []
    ensure_schema(db)

    ref_dir = data_dir / "reference"
    gpu_ref = _load_json(ref_dir / "gpu_reference.json", {"gpus": []})
    pcai_ref = _load_json(ref_dir / "pcai_configurations.json", {"configurations": []})
    research = _load_json(ref_dir / "gpu_device_validation.json", {})
    if not gpu_ref.get("gpus"):
        warnings.append("reference/gpu_reference.json missing or empty: GPU matching will be limited")
    if not research:
        warnings.append("reference/gpu_device_validation.json missing: run tools/validate_gpu_ids.py")

    gpu_docs, device_index, alias_index = build_gpu_docs(gpu_ref, pcai_ref)
    gpu_cc: dict[str, list[str]] = defaultdict(list)
    for g in gpu_docs:
        gpu_cc[g["compute_capability"]].append(g["_id"])
    pcai_docs = build_pcai_docs(pcai_ref, {g["_id"] for g in gpu_docs}, warnings)

    catalog = _load_json(data_dir / "nim_catalog.json", [])
    profiles_dir = data_dir / "nims_catalog"
    status = _read_status(profiles_dir / "_fetch_status.tsv")

    models, profiles = [], []
    for entry in catalog:
        image = entry["image"]
        folder, fetched_image = folder_for_image(image)
        row = status.get(folder, {})
        manifest_path = profiles_dir / folder / "model_manifest.yaml"
        header, manifest_yaml, raw_profiles = None, None, []
        if manifest_path.exists() and manifest_path.stat().st_size > 0:
            manifest_yaml = manifest_path.read_text(encoding="utf-8", errors="replace")
            try:
                manifest = load_manifest(manifest_yaml)
                raw_profiles = iter_profiles(manifest)
                first = raw_profiles[0] if raw_profiles else {}
                header = {
                    "schema_version": str(manifest.get("schema_version") or "") or None,
                    "model": str(manifest.get("model") or first.get("model") or "") or None,
                    "release": str(manifest.get("release") or first.get("release") or "") or None,
                    "profile_selection_criteria": manifest.get("profile_selection_criteria"),
                    "format": ("profiles-list" if "profiles" in manifest else
                               "manifest-list" if "manifest" in manifest else "profile-id-keys"),
                }
            except Exception as exc:  # noqa: BLE001 - report and continue
                warnings.append(f"{folder}: cannot parse manifest: {exc}")

        seen_ids: set[str] = set()
        model_profiles = []
        for raw in raw_profiles:
            p = normalize_profile(raw)
            pid = p["profile_id"] or "unknown"
            if pid in seen_ids:
                pid = f"{pid}#{len(seen_ids)}"
            seen_ids.add(pid)
            hw = p["hw"]
            resolved: list[str] = []
            for d in hw["gpu_device_ids"]:
                ids = device_index.get(d, [])
                resolved += ids
                if not ids:
                    hw["unresolved"].append(f"device:{d}")
            name_ids: list[str] = []
            for n in hw["gpu_names"]:
                ids = alias_index.get(norm_name(n), [])
                name_ids += ids
                if not ids:
                    hw["unresolved"].append(f"name:{n}")
            dev_ids = list(dict.fromkeys(resolved))
            name_ids = list(dict.fromkeys(name_ids))
            hw["gpu_ids"] = dev_ids if hw["gpu_device_ids"] else name_ids
            hw["name_gpu_ids"] = name_ids
            if hw["gpu_device_ids"] and hw["gpu_names"]:
                if dev_ids and name_ids:
                    hw["consistency"] = "consistent" if set(dev_ids) & set(name_ids) else "conflict"
                else:
                    hw["consistency"] = "unresolved"
            else:
                hw["consistency"] = None
            p.update({"_id": f"{folder}/{pid}", "model_id": folder, "profile_id": pid})
            model_profiles.append(to_jsonable(p))

        labels = entry.get("labels") or {}
        meta = entry.get("metadata") or {}
        is_llm = any(p["is_llm"] for p in model_profiles)
        fetch_status = row.get("status") or ("ok" if model_profiles else "missing")
        fetch_message = row.get("message") or None
        if fetch_status not in ("ok", "skipped", "failed", "missing"):
            fetch_status = "failed"
        if fetch_status == "failed" and model_profiles:
            # The finder downloaded the manifest but its regex only knows 64-hex profile ids;
            # the importer parses the YAML itself, so the profiles are usable.
            fetch_status = "ok"
            fetch_message = f"profiles parsed by the importer (finder script reported: {fetch_message})"
        elif fetch_status == "failed" and manifest_yaml and not model_profiles:
            fetch_message = f"manifest has no usable profiles (finder: {fetch_message})"
        repo_tag = fetched_image.rsplit("/", 1)[-1]
        models.append({
            "_id": folder,
            "name": entry.get("name") or folder,
            "display_name": entry.get("displayName") or entry.get("name") or folder,
            "description": entry.get("description"),
            "image": image,
            "fetched_image": fetched_image,
            "source": "internal-mirror" if image.startswith(INTERNAL_PREFIX) else "nvcr.io",
            "publisher": (labels.get("publisher") or [None])[0],
            "repository": repo_tag.split(":", 1)[0],
            "tag": repo_tag.split(":", 1)[1] if ":" in repo_tag else "latest",
            "latest_version": entry.get("latestVersionIdStr") or None,
            "size_bytes": _num(entry.get("latestVersionSizeInBytes")),
            "category": categorize(entry, is_llm),
            "labels": labels,
            "catalog_gpu_labels": [l for l in labels.get("general", []) if GPU_LABEL.search(l)],
            "logo": meta.get("logo") or None,
            "latest_tag_pushed": meta.get("latestTagPushedDate") or None,
            "signed": (meta.get("hasSignedTag") == "true") if meta.get("hasSignedTag") else None,
            "manifest": header,
            "manifest_yaml": manifest_yaml,
            "profile_count": len(model_profiles),
            "gpu_device_ids": sorted({d for p in model_profiles for d in p["hw"]["gpu_device_ids"]}),
            "gpu_ids": sorted({g for p in model_profiles for g in p["hw"]["gpu_ids"]}),
            "compute_capabilities": sorted({p["hw"]["compute_capability"] for p in model_profiles
                                            if p["hw"]["compute_capability"]}),
            "engines": sorted({p["engine"] for p in model_profiles if p["engine"]}),
            "precisions": sorted({p["precision"] for p in model_profiles if p["precision"]}),
            "is_llm": is_llm,
            "fetch": {
                "status": fetch_status,
                "message": fetch_message,
                "seconds": _num(row.get("seconds")),
                "profiles_reported": _num(row.get("profiles")),
            },
        })
        profiles.extend(model_profiles)

    validation = build_validation(profiles, device_index, alias_index, gpu_cc, research)
    issues = build_issue_docs(research)

    # Replace collection contents (small dataset; keeps the import idempotent)
    for name, docs in (("gpus", gpu_docs), ("pcai_configurations", pcai_docs), ("models", models),
                       ("profiles", profiles), ("gpu_device_validation", validation), ("manifest_issues", issues)):
        db[name].delete_many({})
        for i in range(0, len(docs), 1000):
            if docs[i:i + 1000]:
                db[name].insert_many(docs[i:i + 1000], ordered=True)

    counts = {
        "catalog_entries": len(catalog),
        "models": len(models),
        "models_with_profiles": sum(1 for m in models if m["profile_count"]),
        "profiles": len(profiles),
        "gpus": len(gpu_docs),
        "pcai_configurations": len(pcai_docs),
        "validation_rows": len(validation),
        "manifest_issues": len(issues),
        "fetch_status": dict(Counter(m["fetch"]["status"] for m in models)),
        "validation_status": dict(Counter(f"{v['kind']}:{v['status']}" for v in validation)),
    }
    run = {"_id": started.strftime("%Y%m%dT%H%M%SZ"), "started_at": started,
           "finished_at": datetime.now(timezone.utc), "counts": counts, "warnings": warnings}
    db.import_runs.insert_one(run)
    return run


def main() -> int:
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGO_DB", "nim_advisor")
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    client = MongoClient(uri, serverSelectionTimeoutMS=60000)
    for attempt in range(30):
        try:
            client.admin.command("ping")
            break
        except Exception:  # noqa: BLE001
            print(f"waiting for MongoDB ({attempt + 1}/30) ...", flush=True)
            time.sleep(2)
    run = run_import(client[db_name], data_dir)
    print(json.dumps({k: v for k, v in run.items() if k not in ("started_at", "finished_at")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
