"""NIM Profile Advisor - FastAPI application (JSON API + static website)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo import MongoClient

from . import fetcher
from .importer import IMPORT_MODES, run_import, wait_for_mongo
from .recommender import GOALS, Store, gpu_summary, model_detail, recommend, resolve_gpu

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "nim_advisor")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
# Import the data bundled in the image when the website starts (Kubernetes / PCAI); docker compose uses the
# importer service instead. off | always | if-changed
SEED_IMPORT = os.environ.get("SEED_IMPORT", "off").strip().lower()

log = logging.getLogger("uvicorn.error")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000, tz_aware=True)
db = client[MONGO_DB]
store = Store(db)
jobs = fetcher.JobManager(db, on_change=store.invalidate)
seed = {"mode": SEED_IMPORT, "status": "pending" if SEED_IMPORT in IMPORT_MODES else "off", "message": None}


def _seed_import() -> None:
    for attempt in range(1, 11):
        seed.update(status="running", message=None if attempt == 1 else f"attempt {attempt}")
        try:
            wait_for_mongo(client, 300, log=log.info)
            run = run_import(db, DATA_DIR, SEED_IMPORT)
            seed.update(status="done" if run else "unchanged",
                        message=f"import run {run['_id']}" if run else "bundled data unchanged since the last import")
            store.invalidate()
            log.info("seed import (%s): %s", SEED_IMPORT, seed["message"])
            return
        except Exception as exc:  # noqa: BLE001 - retried, reported in /api/stats
            log.exception("seed import failed (attempt %s)", attempt)
            seed.update(status="failed", message=f"{exc.__class__.__name__}: {exc}"[:300])
            time.sleep(min(60, 10 * attempt))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if seed["status"] == "pending":
        threading.Thread(target=_seed_import, name="seed-import", daemon=True).start()
    yield
    jobs.shutdown()


app = FastAPI(title="NIM Profile Advisor for HPE PCAI", version="1.1.0", lifespan=lifespan)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds")
    return str(value)


def _clean(doc):
    """Make Mongo documents JSON friendly."""
    return json.loads(json.dumps(doc, default=_json_default))


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
        "seed": seed,
        "import_enabled": fetcher.IMPORT_ENABLED,
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
                                            "fetched_image", "tag", "latest_version", "size_bytes", "profile_count",
                                            "gpu_device_ids", "gpu_ids", "compute_capabilities", "engines",
                                            "precisions", "fetch", "catalog_gpu_labels", "manifest", "logo",
                                            "publisher", "source", "origin")})
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


# ---------------------------------------------------------------------------
# Load NIMs: fetch profiles of one image or of a catalog JSON (see fetcher.py)
# ---------------------------------------------------------------------------

class ImageRequest(BaseModel):
    image: str = Field(..., min_length=1, max_length=500, description="image, docker pull command or NGC URL")
    display_name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=4000)


class CatalogRequest(BaseModel):
    data: Any = Field(..., description="catalog JSON (nim_catalog.json format) or a list of images")
    force: bool = Field(False, description="re-fetch images that already have profiles")
    source_name: str | None = Field(None, max_length=200)


def _check_import_allowed() -> None:
    if not fetcher.IMPORT_ENABLED:
        raise HTTPException(403, "Loading NIMs is disabled on this server (IMPORT_ENABLED=false)")
    cfg = fetcher.config()
    if not cfg["finder_available"]:
        missing = [t for t, ok in cfg["tools"].items() if not ok]
        raise HTTPException(503, "nim_profile_finder_v2.sh cannot run here: " +
                            (f"missing {', '.join(missing)}" if missing else "script not found (FINDER_SCRIPT)"))


def _catalog_rows(req: CatalogRequest) -> tuple[list[dict], list[dict]]:
    try:
        entries, problems = fetcher.normalize_entries(req.data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return fetcher.classify(db, entries, req.force), problems


@app.get("/api/import/config")
def import_config():
    return fetcher.config()


@app.post("/api/import/image", status_code=202)
def import_image(req: ImageRequest):
    _check_import_allowed()
    try:
        image = fetcher.parse_image_input(req.image)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _clean(jobs.create_single(image, req.display_name, req.description))


@app.post("/api/import/preview")
def import_preview(req: CatalogRequest):
    rows, problems = _catalog_rows(req)
    counts = Counter(r["state"] for r in rows)
    counts["invalid"] = len(problems)
    return _clean({"total": len(rows) + len(problems), "to_fetch": counts["new"] + counts["retry"] + counts["refresh"],
                   "counts": counts, "rows": [{k: v for k, v in r.items() if k != "entry"} for r in rows],
                   "problems": problems})


@app.post("/api/import/bulk", status_code=202)
def import_bulk(req: CatalogRequest):
    _check_import_allowed()
    rows, problems = _catalog_rows(req)
    if not any(r["state"] in ("new", "retry", "refresh") for r in rows):
        raise HTTPException(422, "Nothing to fetch: every image already has profiles in the database "
                                 "(tick 'Re-fetch NIMs that already have profiles' to refresh them)")
    return _clean(jobs.create_job("bulk", rows, req.force, problems=problems, source_name=req.source_name))


@app.get("/api/import/jobs")
def import_jobs(limit: int = Query(20, ge=1, le=100)):
    return _clean(jobs.list_jobs(limit))


@app.get("/api/import/jobs/{job_id}")
def import_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _clean(job)


@app.get("/api/import/jobs/{job_id}/items/{index}/log", response_class=PlainTextResponse)
def import_job_log(job_id: str, index: int):
    text = jobs.item_log(job_id, index)
    if text is None:
        raise HTTPException(404, "no log for this image")
    return text


@app.post("/api/import/jobs/{job_id}/cancel")
def import_job_cancel(job_id: str):
    if not jobs.cancel(job_id):
        raise HTTPException(409, "the job is not running")
    return {"cancel_requested": True}


@app.post("/api/import/jobs/{job_id}/retry", status_code=202)
def import_job_retry(job_id: str):
    _check_import_allowed()
    job = jobs.retry(job_id)
    if not job:
        raise HTTPException(409, "nothing to retry: the job is still running or every image succeeded")
    return _clean(job)


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
