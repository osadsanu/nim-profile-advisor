"""NIM Profile Advisor - FastAPI application (JSON API + static website)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient

from .recommender import GOALS, Store, gpu_summary, model_detail, recommend, resolve_gpu

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "nim_advisor")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000, tz_aware=True)
db = client[MONGO_DB]
store = Store(db)

app = FastAPI(title="NIM Profile Advisor for HPE PCAI", version="1.0.0")


def _clean(doc):
    """Make Mongo documents JSON friendly."""
    return json.loads(json.dumps(doc, default=str))


@app.get("/api/health")
def health():
    client.admin.command("ping")
    return {"status": "ok"}


@app.get("/api/stats")
def stats():
    store.refresh()
    run = db.import_runs.find_one(sort=[("started_at", -1)])
    categories = {}
    for m in store.models.values():
        categories[m["category"]] = categories.get(m["category"], 0) + 1
    return _clean({
        "import_run": run,
        "models": len(store.models),
        "profiles": sum(len(v) for v in store.profiles.values()),
        "gpus": len(store.gpus),
        "pcai_configurations": len(store.pcai),
        "categories": dict(sorted(categories.items())),
        "goals": GOALS,
    })


@app.get("/api/gpus")
def gpus():
    store.refresh()
    items = [gpu_summary(store, g) for g in store.gpus.values()]
    items.sort(key=lambda g: (not g["in_pcai"], g["architecture"], g["name"]))
    return items


@app.get("/api/gpus/resolve")
def gpus_resolve(q: str = Query(..., min_length=1, max_length=120)):
    store.refresh()
    return [gpu_summary(store, g) for g in resolve_gpu(store, q)[:12]]


@app.get("/api/pcai")
def pcai():
    store.refresh()
    out = []
    for c in store.pcai:
        g = store.gpus.get(c["gpu_id"])
        out.append({**c, "gpu": gpu_summary(store, g) if g else None})
    return _clean(out)


@app.get("/api/models")
def models(q: str | None = Query(None, max_length=120), category: str | None = None, status: str | None = None):
    store.refresh()
    needle = (q or "").strip().lower()
    items = []
    for m in store.models.values():
        if category and m["category"] != category:
            continue
        if status and m["fetch"]["status"] != status:
            continue
        if needle and needle not in " ".join([m["_id"], m["name"], m["display_name"], m.get("description") or "",
                                               " ".join(m.get("gpu_device_ids", []))]).lower():
            continue
        items.append({k: m.get(k) for k in ("_id", "name", "display_name", "description", "category", "image",
                                            "fetched_image", "latest_version", "size_bytes", "profile_count",
                                            "gpu_device_ids", "gpu_ids", "compute_capabilities", "engines",
                                            "precisions", "fetch", "catalog_gpu_labels", "manifest", "logo",
                                            "publisher", "source")})
    items.sort(key=lambda m: m["display_name"].lower())
    return _clean(items)


@app.get("/api/models/{model_id}")
def model(model_id: str, gpu: str | None = None, count: int = Query(1, ge=1, le=512),
          nodes: int = Query(1, ge=1, le=64), goal: str = "nim", mig: str | None = None, lora: bool = False,
          vram_gb: float | None = Query(None, gt=0, le=2048)):
    detail = model_detail(store, model_id, gpu, count, nodes, goal, mig, lora, vram_gb)
    if not detail:
        raise HTTPException(404, "model not found")
    return _clean(detail)


@app.get("/api/models/{model_id}/manifest", response_class=PlainTextResponse)
def manifest(model_id: str):
    doc = db.models.find_one({"_id": model_id}, {"manifest_yaml": 1})
    if not doc or not doc.get("manifest_yaml"):
        raise HTTPException(404, "manifest not available")
    return doc["manifest_yaml"]


@app.get("/api/models/{model_id}/profiles/{profile_id}")
def profile(model_id: str, profile_id: str):
    doc = db.profiles.find_one({"_id": f"{model_id}/{profile_id}"})
    if not doc:
        raise HTTPException(404, "profile not found")
    return _clean(doc)


@app.get("/api/recommend")
def api_recommend(gpu: str = Query(..., min_length=1, max_length=120), count: int = Query(1, ge=1, le=512),
                  nodes: int = Query(1, ge=1, le=64), goal: str = "nim", category: str | None = None,
                  q: str | None = Query(None, max_length=120), mig: str | None = Query(None, max_length=40),
                  vram_gb: float | None = Query(None, gt=0, le=2048), lora: bool = False):
    if mig and not re.fullmatch(r"[0-9a-zA-Z.+_-]+", mig):
        raise HTTPException(422, "invalid MIG profile")
    result = recommend(store, gpu, count, nodes, goal, category, q, mig, vram_gb, lora)
    if "error" in result:
        return JSONResponse(status_code=422, content=_clean(result))
    return _clean(result)


@app.get("/api/validation")
def validation(kind: str | None = None, status: str | None = None):
    store.refresh()
    rows = [v for v in store.validation.values()
            if (not kind or v["kind"] == kind) and (not status or v["status"] == status)]
    order = {"conflict": 0, "unknown": 1, "mapped": 2, "validated": 3}
    rows.sort(key=lambda v: (v["kind"], order.get(v["status"], 9), -v["profile_count"]))
    for v in rows:
        v["gpu_names"] = [store.gpus[i]["name"] for i in v.get("gpu_ids", []) if i in store.gpus]
    return _clean(rows)


@app.get("/api/validation/issues")
def validation_issues(kind: str | None = None):
    query = {"kind": kind} if kind else {}
    order = {"unknown_device_id": 0, "device_name_mismatch": 1, "unresolved_name": 2}
    rows = list(db.manifest_issues.find(query))
    rows.sort(key=lambda r: (order.get(r["kind"], 9), -r.get("profiles", 0)))
    return _clean(rows)


@app.get("/api/rules")
def rules():
    path = DATA_DIR / "reference" / "nim_selection_rules.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
