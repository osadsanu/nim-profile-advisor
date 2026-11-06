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
    url = BASE + path + (f"?{urllib.parse.urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
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


def main() -> None:
    wait_until_healthy()

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

    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
    print("all checks passed")


if __name__ == "__main__":
    main()
