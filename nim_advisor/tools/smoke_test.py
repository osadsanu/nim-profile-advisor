"""Smoke-test a running NIM Profile Advisor (standard library only).

Usage:  python tools/smoke_test.py [base_url]      (default http://localhost:8080)
Exits 1 when a check fails. Used by CI and handy after a deployment.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
failures: list[str] = []


def get(path: str, **params) -> tuple[int, object]:
    return request(path + (f"?{urllib.parse.urlencode(params)}" if params else ""))


def post(path: str, body: object) -> tuple[int, object]:
    return request(path, json.dumps(body).encode("utf-8"))


def request(path: str, data: bytes | None = None) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8")
            status = r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode("utf-8"), e.code
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def check(ok: bool, message: str) -> None:
    print(("ok    " if ok else "FAIL  ") + message)
    if not ok:
        failures.append(message)


def wait_until_healthy(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if get("/api/health")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    sys.exit(f"{BASE} did not become healthy within {seconds}s")


def wait_until_seeded(seconds: int = 600) -> None:
    """Kubernetes / PCAI: the website imports the bundled catalog in the background (SEED_IMPORT)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, stats = get("/api/stats")
        seed = (stats.get("seed") or {}) if isinstance(stats, dict) else {}
        if status == 200 and seed.get("status") not in ("pending", "running"):
            return
        time.sleep(3)
    sys.exit(f"the start-up import did not finish within {seconds}s")


def main() -> None:
    wait_until_healthy()
    wait_until_seeded()

    status, page = get("/")
    check(status == 200 and "<title>" in str(page), "website index page")

    status, stats = get("/api/stats")
    run = stats.get("import_run") or {}
    check(status == 200 and stats["models"] > 0 and stats["profiles"] > 0,
          f"stats: {stats.get('models')} models, {stats.get('profiles')} profiles")
    check(stats["gpus"] > 0 and stats["pcai_configurations"] > 0,
          f"reference data: {stats.get('gpus')} GPUs, {stats.get('pcai_configurations')} PCAI configurations")
    check(not run.get("warnings"), f"import warnings: {run.get('warnings') or 'none'}")

    status, pcai = get("/api/pcai")
    check(status == 200 and all(c.get("gpu") for c in pcai), "every PCAI configuration resolves to a GPU")
    per_gpu: dict[str, int] = {}
    for c in pcai:
        per_gpu[c["gpu_id"]] = max(per_gpu.get(c["gpu_id"], 0), int(c["gpus_per_worker_node"] or 1))
    for gpu_id, count in sorted(per_gpu.items()):
        status, rec = get("/api/recommend", gpu=gpu_id, count=count)
        compatible = rec.get("summary", {}).get("compatible", 0) if isinstance(rec, dict) else 0
        check(status == 200 and compatible > 0, f"recommend {gpu_id} x{count}: {compatible} compatible NIMs")

    status, rec = get("/api/recommend", gpu="H200", count=4)
    check(status == 422 and len(rec.get("gpu_candidates", [])) > 1, "ambiguous GPU 'H200' asks to pick a model")

    status, issues = get("/api/validation/issues")
    check(status == 200 and isinstance(issues, list), f"validation issues endpoint ({len(issues)} issues)")

    # Load NIMs: configuration and input checks only (no NGC download)
    status, cfg = get("/api/import/config")
    check(status == 200 and "finder_available" in cfg,
          f"import config: enabled={cfg.get('enabled')} finder={cfg.get('finder_available')} "
          f"ngc_key={cfg.get('ngc_configured')}")
    status, _ = post("/api/import/image", {"image": "not an image"})
    check(status in (422, 403, 503), f"invalid image is rejected ({status})")
    status, models = get("/api/models")
    known = next((m["image"] for m in models if m.get("profile_count")), None) if status == 200 else None
    status, preview = post("/api/import/preview", {"data": [known, "nvcr.io/nim/example/new-nim:1.0", {"name": "x"}]})
    counts = preview.get("counts", {}) if isinstance(preview, dict) else {}
    check(status == 200 and counts.get("skip") == 1 and counts.get("new") == 1 and counts.get("invalid") == 1,
          f"catalog preview classifies entries ({dict(counts)})")
    status, jobs = get("/api/import/jobs")
    check(status == 200 and isinstance(jobs, list), f"import jobs endpoint ({len(jobs)} jobs)")

    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
    print("all checks passed")


if __name__ == "__main__":
    main()
