"""MongoDB schema: collections, $jsonSchema validators and indexes.

Collections ("tables")
----------------------
models                 one document per NIM catalog entry (304), incl. fetch status and manifest header
profiles               one document per NIM profile (normalized fields + raw tags + workspace file list)
gpus                   GPU reference catalog (PCI device ids, VRAM, compute capability, aliases)
pcai_configurations    HPE Private Cloud AI system configurations (GPU model, GPUs per node, MIG ...)
gpu_device_validation  evidence that each NIM gpu_device / internal gpu name maps to the right GPU
manifest_issues        manifest errors found by tools/validate_gpu_ids.py (gpu name vs gpu_device, invalid ids)
import_runs            audit trail of every import (full imports and website updates)
catalog_additions      NIM images loaded from the website ("Load NIMs" tab): catalog entry + fetched manifest.
                       Merged into every full import, so they survive re-imports
import_jobs            website fetch jobs (one image or a bulk catalog JSON) and the result of every image
import_logs            finder output of every fetched image (tail, NGC key redacted)
locks                  short-lived lease that serializes database writes of the importer and the website
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, TEXT, IndexModel
from pymongo.database import Database
from pymongo.errors import CollectionInvalid

STR = {"bsonType": "string"}
STR_N = {"bsonType": ["string", "null"]}
NUM = {"bsonType": ["int", "long", "double", "decimal"]}
NUM_N = {"bsonType": ["int", "long", "double", "decimal", "null"]}
INT = {"bsonType": ["int", "long"]}
BOOL = {"bsonType": "bool"}
BOOL_N = {"bsonType": ["bool", "null"]}
ARR_STR = {"bsonType": "array", "items": STR}
OBJ = {"bsonType": "object"}
OBJ_N = {"bsonType": ["object", "null"]}

VALIDATORS: dict[str, dict] = {
    "models": {
        "bsonType": "object",
        "required": ["_id", "name", "display_name", "image", "fetched_image", "category", "fetch", "profile_count"],
        "properties": {
            "_id": STR,                      # folder name, e.g. llama-3.1-8b-instruct_latest
            "name": STR,
            "display_name": STR,
            "description": STR_N,
            "image": STR,                    # image exactly as in nim_catalog.json
            "fetched_image": STR,            # image actually inspected (nvcr.io)
            "source": {"enum": ["nvcr.io", "internal-mirror"]},
            "publisher": STR_N,
            "repository": STR,
            "tag": STR,
            "latest_version": STR_N,
            "size_bytes": NUM_N,
            "category": STR,
            "labels": OBJ,
            "catalog_gpu_labels": ARR_STR,
            "logo": STR_N,
            "latest_tag_pushed": STR_N,
            "signed": BOOL_N,
            "manifest": OBJ_N,
            "manifest_yaml": STR_N,
            "profile_count": INT,
            "gpu_device_ids": ARR_STR,
            "gpu_ids": ARR_STR,
            "compute_capabilities": ARR_STR,
            "engines": ARR_STR,
            "precisions": ARR_STR,
            "is_llm": BOOL,
            "origin": {"enum": ["catalog", "website"]},  # website = loaded or refreshed from the Load NIMs tab
            "fetch": {
                "bsonType": "object",
                "required": ["status"],
                "properties": {
                    "status": {"enum": ["ok", "skipped", "failed", "missing"]},
                    "message": STR_N,
                    "seconds": NUM_N,
                    "profiles_reported": NUM_N,
                },
            },
        },
    },
    "profiles": {
        "bsonType": "object",
        "required": ["_id", "model_id", "profile_id", "tags", "hw", "tp", "pp",
                     "gpus_required", "nodes_required", "gpus_per_node_required"],
        "properties": {
            "_id": STR,                      # <model_id>/<profile_id>
            "model_id": STR,
            "profile_id": STR,
            "tags": OBJ,                     # raw manifest tags (strings)
            "hw": {
                "bsonType": "object",
                "required": ["match_mode", "gpu_device_ids", "gpu_names", "gpu_ids"],
                "properties": {
                    "match_mode": {"enum": ["gpu_device", "gpu_name", "compute_capability", "generic"]},
                    "gpu_device_raw": STR_N,
                    "gpu_device_ids": ARR_STR,
                    "gpu_device_invalid": ARR_STR,
                    "gpu_names": ARR_STR,
                    "gpu_name_generic": BOOL,
                    "compute_capability": STR_N,
                    "gpu_ids": ARR_STR,
                    "name_gpu_ids": ARR_STR,     # GPUs denoted by the internal gpu name(s)
                    "unresolved": ARR_STR,
                    "consistency": STR_N,
                },
            },
            "tp": INT,
            "pp": INT,
            "gpus_required": INT,
            "nodes_required": INT,
            "gpus_per_node_required": INT,
            "precision": STR_N,
            "engine": STR_N,
            "is_llm": BOOL,
            "profile_type": {"enum": ["latency", "throughput", None]},
            "profile_tag": STR_N,
            "nim_optimized": BOOL,
            "trtllm_buildable": BOOL,
            "feat_lora": BOOL_N,
            "min_vram_gb": NUM_N,
            "nim_description": STR_N,
            "workspace_file_count": INT,
            "workspace_hash": STR_N,
            "workspace": {"bsonType": "array"},
        },
    },
    "gpus": {
        "bsonType": "object",
        "required": ["_id", "name", "pci_device_ids", "compute_capability", "architecture", "aliases"],
        "properties": {
            "_id": STR,                      # e.g. h200-nvl
            "name": STR,
            "family": STR_N,
            "chip": STR_N,
            "architecture": STR,
            "compute_capability": STR,
            "vram_gb": NUM_N,
            "form_factor": STR_N,
            "pci_device_ids": ARR_STR,
            "nim_gpu_device": ARR_STR,
            "aliases": ARR_STR,
            "aliases_norm": ARR_STR,
            "nvidia_smi_names": ARR_STR,
            "internal_names": ARR_STR,
            "k8s_gpu_product": ARR_STR,
            "fp8": BOOL_N,
            "nvfp4": BOOL_N,
            "nvlink": STR_N,
            "in_pcai": BOOL,
            "confidence": {"enum": ["high", "medium", "low"]},
            "sources": ARR_STR,
        },
    },
    "pcai_configurations": {
        "bsonType": "object",
        "required": ["_id", "name", "size", "gpu_id", "gpus_per_worker_node"],
        "properties": {
            "_id": STR,
            "name": STR,
            "generation": STR_N,             # G1 (legacy) / G2 (current)
            "size": STR,
            "gpu_id": STR,
            "gpu_model": STR_N,
            "gpus_per_worker_node": NUM,
            "gpu_worker_nodes": NUM_N,
            "worker_node_options": {"bsonType": "array", "items": NUM},
            "total_gpus": NUM_N,
            "total_gpu_options": {"bsonType": "array", "items": NUM},
            "control_nodes": NUM_N,
            "server_platform": STR_N,
            "storage": STR_N,
            "ai_network": STR_N,
            "expansion": STR_N,
            "air_gapped": BOOL_N,
            "mig_profiles": ARR_STR,
            "mig_partitions": {"bsonType": "array", "items": OBJ},
            "nvlink": STR_N,
            "status": STR_N,
            "notes": STR_N,
            "sources": ARR_STR,
        },
    },
    "manifest_issues": {
        "bsonType": "object",
        "required": ["_id", "kind", "gpu_name", "profiles", "models", "explanation"],
        "properties": {
            "_id": STR,
            "kind": {"enum": ["device_name_mismatch", "unknown_device_id", "unresolved_name"]},
            "gpu_device": STR_N,
            "gpu_name": STR,
            "device_gpu_ids": ARR_STR,
            "name_gpu_ids": ARR_STR,
            "profiles": INT,
            "models": ARR_STR,
            "explanation": STR,
            "locations": ARR_STR,
        },
    },
    "gpu_device_validation": {
        "bsonType": "object",
        "required": ["_id", "kind", "status"],
        "properties": {
            "_id": STR,                      # "device:2335" or "name:h200-nvl" or "cc:8.9"
            "kind": {"enum": ["gpu_device", "gpu_name", "compute_capability"]},
            "value": STR,
            "status": {"enum": ["validated", "mapped", "conflict", "unknown"]},
            "gpu_ids": ARR_STR,
            "profile_count": INT,
            "model_count": INT,
            "cooccurrence": {"bsonType": "array"},
            "research": OBJ_N,
            "notes": ARR_STR,
        },
    },
    "import_runs": {
        "bsonType": "object",
        "required": ["_id", "started_at", "finished_at", "counts"],
        "properties": {
            "_id": STR,
            "kind": {"enum": ["full", "website"]},
            "started_at": {"bsonType": "date"},
            "finished_at": {"bsonType": "date"},
            "counts": OBJ,
            "warnings": ARR_STR,
            "fingerprint": STR,              # hash of the imported files (IMPORT_MODE=if-changed)
            "job_id": STR_N,                 # website update: job and model it applied
            "model_id": STR_N,
        },
    },
    "catalog_additions": {
        "bsonType": "object",
        "required": ["_id", "image", "fetched_image", "entry", "fetch", "updated_at"],
        "properties": {
            "_id": STR,                      # model folder name, same as models._id
            "image": STR,
            "fetched_image": STR,
            "entry": OBJ,                    # catalog entry (nim_catalog.json format, sanitized)
            "manifest_yaml": STR_N,          # last successfully fetched model_manifest.yaml
            "fetch": {
                "bsonType": "object",
                "required": ["status"],
                "properties": {
                    "status": {"enum": ["ok", "failed"]},
                    "message": STR_N,
                    "seconds": NUM_N,
                    "profiles_reported": NUM_N,
                },
            },
            "source": STR,
            "job_id": STR_N,
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "fetched_at": {"bsonType": ["date", "null"]},
        },
    },
    "import_jobs": {
        "bsonType": "object",
        "required": ["_id", "kind", "status", "created_at", "total", "items"],
        "properties": {
            "_id": STR,
            "kind": {"enum": ["single", "bulk"]},
            "status": {"enum": ["queued", "running", "done", "cancelled", "interrupted"]},
            "created_at": {"bsonType": "date"},
            "started_at": {"bsonType": ["date", "null"]},
            "finished_at": {"bsonType": ["date", "null"]},
            "heartbeat_at": {"bsonType": ["date", "null"]},
            "owner": STR,
            "force": BOOL,
            "cancel_requested": BOOL,
            "total": INT,
            "counts": OBJ,
            "source_name": STR_N,            # uploaded file name of a bulk import
            "retry_of": STR_N,
            "problems": {"bsonType": "array"},  # catalog entries rejected before the job started
            "items": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["index", "folder", "image", "fetched_image", "status"],
                    "properties": {
                        "index": INT,
                        "folder": STR,
                        "name": STR_N,
                        "image": STR,
                        "fetched_image": STR,
                        "status": {"enum": ["queued", "running", "ok", "failed", "skipped", "cancelled",
                                            "interrupted"]},
                        "action": STR_N,     # added / updated / kept
                        "profiles": NUM_N,
                        "seconds": NUM_N,
                        "message": STR_N,
                        "entry": OBJ,
                        "has_log": BOOL,
                    },
                },
            },
        },
    },
    "import_logs": {
        "bsonType": "object",
        "required": ["_id", "job_id", "index", "log"],
        "properties": {
            "_id": STR,                      # <job id>/<item index>
            "job_id": STR,
            "index": INT,
            "log": STR,                      # tail of the finder output (NGC key redacted)
            "created_at": {"bsonType": "date"},
        },
    },
    "locks": {
        "bsonType": "object",
        "required": ["_id", "owner", "expires_at"],
        "properties": {
            "_id": STR,
            "owner": STR,
            "acquired_at": {"bsonType": "date"},
            "expires_at": {"bsonType": "date"},
        },
    },
}

INDEXES: dict[str, list[IndexModel]] = {
    "models": [
        IndexModel([("name", ASCENDING)], name="name", unique=True),
        IndexModel([("category", ASCENDING)], name="category"),
        IndexModel([("fetch.status", ASCENDING)], name="fetch_status"),
        IndexModel([("gpu_device_ids", ASCENDING)], name="gpu_device_ids"),
        IndexModel([("display_name", TEXT), ("name", TEXT), ("description", TEXT)], name="text_search"),
    ],
    "profiles": [
        IndexModel([("model_id", ASCENDING)], name="model_id"),
        IndexModel([("hw.match_mode", ASCENDING), ("gpus_per_node_required", ASCENDING)], name="match_mode_gpus"),
        IndexModel([("hw.gpu_device_ids", ASCENDING)], name="gpu_device_ids"),
        IndexModel([("hw.gpu_ids", ASCENDING)], name="gpu_ids"),
        IndexModel([("hw.compute_capability", ASCENDING)], name="compute_capability"),
        IndexModel([("precision", ASCENDING)], name="precision"),
        IndexModel([("engine", ASCENDING)], name="engine"),
    ],
    "gpus": [
        IndexModel([("pci_device_ids", ASCENDING)], name="pci_device_ids"),
        IndexModel([("aliases_norm", ASCENDING)], name="aliases_norm"),
    ],
    "pcai_configurations": [
        IndexModel([("gpu_id", ASCENDING)], name="gpu_id"),
    ],
    "gpu_device_validation": [
        IndexModel([("kind", ASCENDING), ("status", ASCENDING)], name="kind_status"),
    ],
    "manifest_issues": [
        IndexModel([("kind", ASCENDING)], name="kind"),
        IndexModel([("models", ASCENDING)], name="models"),
    ],
    "import_runs": [
        IndexModel([("started_at", DESCENDING)], name="started_at"),
    ],
    "catalog_additions": [
        IndexModel([("updated_at", DESCENDING)], name="updated_at"),
    ],
    "import_jobs": [
        IndexModel([("created_at", DESCENDING)], name="created_at"),
        IndexModel([("status", ASCENDING)], name="status"),
    ],
    "import_logs": [
        IndexModel([("job_id", ASCENDING)], name="job_id"),
    ],
}


def ensure_schema(db: Database) -> None:
    """Create collections with validators (or update them) and build indexes. Idempotent."""
    existing = set(db.list_collection_names())
    for name, schema in VALIDATORS.items():
        validator = {"$jsonSchema": schema}
        if name in existing:
            db.command("collMod", name, validator=validator,
                       validationLevel="strict", validationAction="error")
        else:
            try:
                db.create_collection(name, validator=validator,
                                     validationLevel="strict", validationAction="error")
            except CollectionInvalid:
                pass
    for name, indexes in INDEXES.items():
        db[name].create_indexes(indexes)
