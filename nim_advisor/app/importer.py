"""Import nim_catalog.json + nims_catalog/<model>/model_manifest.yaml + reference data into MongoDB.

Usage (inside the container):  python -m app.importer
Environment:
    MONGO_URI       mongodb://user:pass@mongo:27017/?authSource=admin
    MONGO_DB        nim_advisor
    DATA_DIR        /data   (contains nim_catalog.json, nims_catalog/, reference/; the image bundles a copy)
    IMPORT_MODE     always (default) | if-changed: skip when the files and the import code did not change
    MONGO_WAIT_SECONDS  how long to wait for MongoDB (default 60)

NIMs loaded from the website (collection catalog_additions) are merged into every full import, so a re-import
never drops them. The website updates single models with apply_model() (same document builder as the import).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from pymongo import MongoClient, ReplaceOne
from pymongo.errors import DuplicateKeyError

from .normalize import (
    INTERNAL_PREFIX, folder_for_image, iter_profiles, load_manifest, norm_name, normalize_profile, to_jsonable,
)
from .schema import ensure_schema

IMPORT_MODES = ("always", "if-changed")

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
        entry.get("name") or "", entry.get("displayName") or "", entry.get("description") or "",
        " ".join(labels.get("general") or []),
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

def build_gpu_docs(ref: dict, pcai: dict) -> list[dict]:
    pcai_gpu_ids = {c.get("gpu_id") for c in pcai.get("configurations", [])}
    docs = []
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
    return docs


def gpu_indexes(gpu_docs: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """PCI device id -> GPU ids, normalized alias -> GPU ids and compute capability -> GPU ids."""
    device_index: dict[str, list[str]] = defaultdict(list)
    alias_index: dict[str, list[str]] = defaultdict(list)
    gpu_cc: dict[str, list[str]] = defaultdict(list)
    for g in gpu_docs:
        gid = g["_id"]
        for i in g.get("pci_device_ids", []):
            if gid not in device_index[i]:
                device_index[i].append(gid)
        for a in g.get("aliases_norm", []):
            if gid not in alias_index[a]:
                alias_index[a].append(gid)
        gpu_cc[str(g.get("compute_capability") or "unknown")].append(gid)
    return dict(device_index), dict(alias_index), dict(gpu_cc)


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
# one model (shared by the full import and the website updates)
# ---------------------------------------------------------------------------

def _non_empty(entry: dict | None) -> dict:
    return {k: v for k, v in (entry or {}).items() if v not in (None, "", [], {})}


def _read_manifest(path: Path) -> str | None:
    if path.is_file() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def addition_row(add: dict) -> dict:
    """Fetch result of a website addition in the shape of a nims_catalog/_fetch_status.tsv row."""
    f = add.get("fetch") or {}
    return {"status": f.get("status") or "failed", "message": f.get("message") or "", "seconds": f.get("seconds"),
            "profiles": f.get("profiles_reported")}


def model_row(model: dict) -> dict:
    """Fetch result of a stored models document in the shape of a nims_catalog/_fetch_status.tsv row."""
    f = model.get("fetch") or {}
    return {"status": f.get("status") or "", "message": f.get("message") or "", "seconds": f.get("seconds"),
            "profiles": f.get("profiles_reported")}


def entry_from_model(model: dict) -> dict:
    """Catalog entry (nim_catalog.json format) of a stored models document."""
    signed = model.get("signed")
    return {
        "name": model.get("name"), "displayName": model.get("display_name"), "description": model.get("description"),
        "image": model["image"], "labels": model.get("labels") or {}, "latestVersionIdStr": model.get("latest_version"),
        "latestVersionSizeInBytes": model.get("size_bytes"),
        "metadata": {"logo": model.get("logo"), "latestTagPushedDate": model.get("latest_tag_pushed"),
                     "hasSignedTag": None if signed is None else ("true" if signed else "false")},
    }


def unique_name(name: str, folder: str, used: set[str]) -> str:
    """models.name is unique: a name already used by another model falls back to the folder name."""
    candidate, n = name, 2
    if candidate in used:
        candidate = folder
    while candidate in used:
        candidate, n = f"{folder}-{n}", n + 1
    used.add(candidate)
    return candidate


def merge_catalog(catalog: list[dict], additions: list[dict], warnings: list[str]) -> list[dict]:
    """Catalog entries + website additions -> [{entry, folder, fetched_image, addition}], catalog order first.

    An addition with the folder of a catalog entry refreshes that entry (its non-empty fields win and its manifest
    replaces the bundled one); any other addition is appended as a new model.
    """
    items: list[dict] = []
    by_folder: dict[str, dict] = {}
    for entry in catalog:
        folder, fetched_image = folder_for_image(entry["image"])
        item = {"entry": entry, "folder": folder, "fetched_image": fetched_image, "addition": None}
        by_folder[folder] = item
        items.append(item)
    for add in sorted(additions, key=lambda a: a["_id"]):
        item = by_folder.get(add["_id"])
        if item and item["fetched_image"] != add.get("fetched_image"):
            warnings.append(f"website addition {add['_id']} ({add.get('fetched_image')}) conflicts with the catalog "
                            f"image {item['fetched_image']}; load it again from the website")
            continue
        if item:
            item["entry"] = {**item["entry"], **_non_empty(add.get("entry"))}
            item["addition"] = add
            continue
        entry = {**_non_empty(add.get("entry")), "image": add["image"]}
        item = {"entry": entry, "folder": add["_id"], "fetched_image": add["fetched_image"], "addition": add}
        by_folder[add["_id"]] = item
        items.append(item)
    return items


def build_model(entry: dict, folder: str, fetched_image: str, manifest_yaml: str | None, row: dict,
                device_index: dict, alias_index: dict, warnings: list[str],
                origin: str = "catalog") -> tuple[dict, list[dict]]:
    """Normalize one catalog entry and its model_manifest.yaml into a models document + its profiles documents.

    row: fetch result of the image with the keys of nims_catalog/_fetch_status.tsv (status, message, seconds,
    profiles).
    """
    image = entry["image"]
    header, raw_profiles = None, []
    manifest_yaml = manifest_yaml or None
    if manifest_yaml:
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
    model = {
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
        "origin": origin,
        "fetch": {
            "status": fetch_status,
            "message": fetch_message,
            "seconds": _num(row.get("seconds")),
            "profiles_reported": _num(row.get("profiles")),
        },
    }
    return model, model_profiles


# ---------------------------------------------------------------------------
# database lock, data fingerprint, counts
# ---------------------------------------------------------------------------

@contextmanager
def db_lock(db, name: str = "import", ttl: int = 60, wait: float = 1800.0) -> Iterator[str]:
    """Lease lock in the 'locks' collection.

    Serializes the writes of full imports and website updates (several web replicas, a manual importer run).
    The lease is renewed while held and expires ttl seconds after its holder died.
    """
    owner = f"{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + wait
    while True:
        now = datetime.now(timezone.utc)
        try:
            db.locks.update_one(
                {"_id": name, "expires_at": {"$lt": now}},
                {"$set": {"owner": owner, "acquired_at": now, "expires_at": now + timedelta(seconds=ttl)}},
                upsert=True)
            break
        except DuplicateKeyError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"database lock '{name}' is still held by another import") from None
            time.sleep(0.5)
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(ttl / 3):
            try:
                db.locks.update_one({"_id": name, "owner": owner},
                                    {"$set": {"expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl)}})
            except Exception:  # noqa: BLE001 - the next renewal retries
                pass

    renewer = threading.Thread(target=renew, name=f"lock-{name}", daemon=True)
    renewer.start()
    try:
        yield owner
    finally:
        stop.set()
        renewer.join(timeout=5)
        db.locks.delete_one({"_id": name, "owner": owner})


CODE_FILES = ("importer.py", "normalize.py", "schema.py")


def data_fingerprint(data_dir: Path) -> str:
    """Hash of every imported file and of the import code (IMPORT_MODE=if-changed)."""
    h = hashlib.sha256(INTERNAL_PREFIX.encode() + b"\0")
    data_files = [data_dir / "nim_catalog.json", data_dir / "nims_catalog" / "_fetch_status.tsv",
                  *sorted((data_dir / "nims_catalog").glob("*/model_manifest.yaml")),
                  *sorted((data_dir / "reference").glob("*.json"))]
    code_dir = Path(__file__).resolve().parent
    for base, paths in ((data_dir, data_files), (code_dir, [code_dir / f for f in CODE_FILES])):
        for p in paths:
            if p.is_file():
                h.update(p.relative_to(base).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def current_counts(db) -> dict:
    """Counts of the stored data, with the keys of a full import run."""
    last_full = db.import_runs.find_one({"kind": {"$ne": "website"}}, sort=[("started_at", -1)]) or {}
    models = db.models.count_documents({})
    fetch = db.models.aggregate([{"$group": {"_id": "$fetch.status", "n": {"$sum": 1}}}])
    validation = db.gpu_device_validation.aggregate(
        [{"$group": {"_id": {"k": "$kind", "s": "$status"}, "n": {"$sum": 1}}}])
    return {
        "catalog_entries": (last_full.get("counts") or {}).get("catalog_entries", models),
        "models": models,
        "models_with_profiles": db.models.count_documents({"profile_count": {"$gt": 0}}),
        "profiles": db.profiles.count_documents({}),
        "gpus": db.gpus.count_documents({}),
        "pcai_configurations": db.pcai_configurations.count_documents({}),
        "validation_rows": db.gpu_device_validation.count_documents({}),
        "manifest_issues": db.manifest_issues.count_documents({}),
        "website_additions": db.catalog_additions.count_documents({}),
        "fetch_status": {str(d["_id"]): d["n"] for d in fetch},
        "validation_status": {f"{d['_id']['k']}:{d['_id']['s']}": d["n"] for d in validation},
    }


def refresh_validation(db, device_index: dict, alias_index: dict, gpu_cc: dict[str, list[str]]) -> int:
    """Rebuild gpu_device_validation from the stored profiles (keeps the research verdicts of the last import)."""
    research: dict[str, dict] = {"gpu_devices": {}, "gpu_names": {}}
    for v in db.gpu_device_validation.find({"research": {"$ne": None}}, {"kind": 1, "value": 1, "research": 1}):
        if v["kind"] == "gpu_device":
            research["gpu_devices"][v["value"].split(":")[0]] = v["research"]
        elif v["kind"] == "gpu_name":
            research["gpu_names"][v["value"]] = v["research"]
    profiles = list(db.profiles.find({}, {"model_id": 1, "hw": 1}))
    docs = build_validation(profiles, device_index, alias_index, gpu_cc, research)
    if docs:
        db.gpu_device_validation.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs],
                                            ordered=False)
    db.gpu_device_validation.delete_many({"_id": {"$nin": [d["_id"] for d in docs]}})
    return len(docs)


# ---------------------------------------------------------------------------
# website update of one model
# ---------------------------------------------------------------------------

def apply_model(db, folder: str, job_id: str | None = None) -> dict:
    """Rebuild one model from the database (stored model + its catalog_additions document) after a website fetch.

    Updates models, profiles and gpu_device_validation and records a 'website' import run, so the website reloads.
    """
    started = datetime.now(timezone.utc)
    warnings: list[str] = []
    with db_lock(db, wait=float(os.environ.get("IMPORT_LOCK_WAIT", "1800"))):
        existing = db.models.find_one({"_id": folder})
        add = db.catalog_additions.find_one({"_id": folder})
        if not existing and not add:
            raise KeyError(f"unknown model {folder}")
        device_index, alias_index, gpu_cc = gpu_indexes(
            list(db.gpus.find({}, {"pci_device_ids": 1, "aliases_norm": 1, "compute_capability": 1})))
        entry = {**(entry_from_model(existing) if existing else {}), **_non_empty((add or {}).get("entry"))}
        entry["image"] = add["image"] if add else existing["image"]
        fetched_image = add["fetched_image"] if add else existing["fetched_image"]
        if add and add.get("manifest_yaml"):
            manifest_yaml, row = add["manifest_yaml"], addition_row(add)
        elif existing and existing.get("manifest_yaml"):
            manifest_yaml, row = existing["manifest_yaml"], model_row(existing)
        else:
            manifest_yaml, row = None, addition_row(add) if add else model_row(existing)
        origin = "website" if add else (existing.get("origin") or "catalog")
        model, profiles = build_model(entry, folder, fetched_image, manifest_yaml, row, device_index, alias_index,
                                      warnings, origin=origin)
        model["name"] = unique_name(model["name"], folder,
                                    set(db.models.distinct("name", {"_id": {"$ne": folder}})))
        db.models.replace_one({"_id": folder}, model, upsert=True)
        db.profiles.delete_many({"model_id": folder})
        if profiles:
            db.profiles.insert_many(profiles, ordered=True)
        refresh_validation(db, device_index, alias_index, gpu_cc)
        now = datetime.now(timezone.utc)
        db.import_runs.insert_one({
            "_id": f"{now:%Y%m%dT%H%M%S}.{now:%f}Z-website", "kind": "website", "started_at": started,
            "finished_at": now, "counts": current_counts(db), "warnings": warnings, "job_id": job_id,
            "model_id": folder,
        })
    return {"action": "updated" if existing else "added",
            "model": {k: v for k, v in model.items() if k != "manifest_yaml"}, "warnings": warnings}


# ---------------------------------------------------------------------------
# full import
# ---------------------------------------------------------------------------

def run_import(db, data_dir: Path, mode: str = "always") -> dict | None:
    """Import the bundled files + the website additions, replacing the collections.

    Returns the import run, or None when mode is 'if-changed' and neither the files nor the import code changed
    since the last full import.
    """
    if mode not in IMPORT_MODES:
        raise ValueError(f"unknown import mode '{mode}' (use {' or '.join(IMPORT_MODES)})")
    with db_lock(db, wait=float(os.environ.get("IMPORT_LOCK_WAIT", "1800"))):
        ensure_schema(db)
        fingerprint = data_fingerprint(data_dir)
        if mode == "if-changed":
            last = db.import_runs.find_one({"fingerprint": {"$exists": True}}, sort=[("started_at", -1)])
            if (last and last.get("fingerprint") == fingerprint and db.models.estimated_document_count()
                    and db.gpus.estimated_document_count()):
                return None
        return _full_import(db, data_dir, fingerprint)


def _full_import(db, data_dir: Path, fingerprint: str) -> dict:
    started = datetime.now(timezone.utc)
    warnings: list[str] = []

    ref_dir = data_dir / "reference"
    gpu_ref = _load_json(ref_dir / "gpu_reference.json", {"gpus": []})
    pcai_ref = _load_json(ref_dir / "pcai_configurations.json", {"configurations": []})
    research = _load_json(ref_dir / "gpu_device_validation.json", {})
    if not gpu_ref.get("gpus"):
        warnings.append("reference/gpu_reference.json missing or empty: GPU matching will be limited")
    if not research:
        warnings.append("reference/gpu_device_validation.json missing: run tools/validate_gpu_ids.py")

    gpu_docs = build_gpu_docs(gpu_ref, pcai_ref)
    device_index, alias_index, gpu_cc = gpu_indexes(gpu_docs)
    pcai_docs = build_pcai_docs(pcai_ref, {g["_id"] for g in gpu_docs}, warnings)

    catalog = _load_json(data_dir / "nim_catalog.json", [])
    profiles_dir = data_dir / "nims_catalog"
    status = _read_status(profiles_dir / "_fetch_status.tsv")
    additions = list(db.catalog_additions.find())

    models, profiles, used_names = [], [], set()
    for item in merge_catalog(catalog, additions, warnings):
        folder, add = item["folder"], item["addition"]
        file_yaml = _read_manifest(profiles_dir / folder / "model_manifest.yaml")
        if add and add.get("manifest_yaml"):
            manifest_yaml, row = add["manifest_yaml"], addition_row(add)
        elif file_yaml:
            manifest_yaml, row = file_yaml, status.get(folder, {})
        else:
            manifest_yaml, row = None, (addition_row(add) if add else status.get(folder, {}))
        model, model_profiles = build_model(item["entry"], folder, item["fetched_image"], manifest_yaml, row,
                                            device_index, alias_index, warnings,
                                            origin="website" if add else "catalog")
        model["name"] = unique_name(model["name"], folder, used_names)
        models.append(model)
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
        "website_additions": len(additions),
        "fetch_status": dict(Counter(m["fetch"]["status"] for m in models)),
        "validation_status": dict(Counter(f"{v['kind']}:{v['status']}" for v in validation)),
    }
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    if db.import_runs.find_one({"_id": run_id}, {"_id": 1}):
        run_id = f"{run_id}-{uuid.uuid4().hex[:4]}"
    run = {"_id": run_id, "kind": "full", "started_at": started, "finished_at": datetime.now(timezone.utc),
           "counts": counts, "warnings": warnings, "fingerprint": fingerprint}
    db.import_runs.insert_one(run)
    return run


def wait_for_mongo(client: MongoClient, seconds: float, log=print) -> None:
    """Ping MongoDB until it answers (raises the last error after `seconds`)."""
    deadline = time.monotonic() + seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            client.admin.command("ping")
            return
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() >= deadline:
                raise
            log(f"waiting for MongoDB ({attempt}): {exc.__class__.__name__}")
            time.sleep(2)


def main() -> int:
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGO_DB", "nim_advisor")
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    mode = os.environ.get("IMPORT_MODE", "always").strip().lower() or "always"
    if mode not in IMPORT_MODES:
        print(f"IMPORT_MODE must be one of: {', '.join(IMPORT_MODES)} (got '{mode}')", file=sys.stderr)
        return 2
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    wait_for_mongo(client, float(os.environ.get("MONGO_WAIT_SECONDS", "60")), log=lambda m: print(m, flush=True))
    run = run_import(client[db_name], data_dir, mode)
    if run is None:
        print("nothing to import: data and import code unchanged since the last import (IMPORT_MODE=if-changed)")
        return 0
    print(json.dumps({k: v for k, v in run.items() if k not in ("started_at", "finished_at")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
