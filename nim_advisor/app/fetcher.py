"""Load NIM profiles from the website ("Load NIMs" tab).

A job fetches one NIM image, or every entry of a catalog JSON in the nim_catalog.json format, with
nim_profile_finder_v2.sh (skopeo: only the model.manifest.yaml label or the manifest layer is downloaded, never the
whole image). Every result is stored in the catalog_additions collection and the model is rebuilt in MongoDB
(importer.apply_model), so the recommender uses it right away and later full imports keep it.

Environment:
    NGC_API_KEY        NGC API key for nvcr.io (empty or a <placeholder> = not configured)
    FINDER_SCRIPT      path of nim_profile_finder_v2.sh (default: /srv or the repository root)
    FETCH_WORKERS      images fetched in parallel (default 4)
    FETCH_TIMEOUT      seconds per image and attempt (default 900)
    FETCH_MAX_FILE_MB  abort an image when the finder writes a larger file (default 1024, as bulk_fetch_profiles.sh)
    IMPORT_ENABLED     false = read-only website, no new jobs
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .importer import apply_model
from .normalize import INTERNAL_PREFIX, folder_for_image
from .schema import ensure_schema

log = logging.getLogger("uvicorn.error")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value not in ("0", "false", "no", "off")


NGC_API_KEY = os.environ.get("NGC_API_KEY", "").strip()
FETCH_WORKERS = _env_int("FETCH_WORKERS", 4, 1, 16)
FETCH_TIMEOUT = _env_int("FETCH_TIMEOUT", 900, 30, 7200)
FETCH_MAX_FILE_MB = _env_int("FETCH_MAX_FILE_MB", 1024, 16, 65536)
IMPORT_ENABLED = _env_bool("IMPORT_ENABLED", True)
MAX_BULK = 2000
KEEP_JOBS = 50                           # finished jobs (and their logs) kept in the database
STALE_AFTER = timedelta(seconds=90)      # a running job without heartbeat for this long was interrupted
OWNER = f"{socket.gethostname()}/{os.getpid()}"
TOOLS = ("bash", "skopeo", "jq", "curl", "file")
PASS_ENV = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
            "https_proxy", "no_proxy")


def _find_finder() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (os.environ.get("FINDER_SCRIPT"), here.parents[1] / "nim_profile_finder_v2.sh",
                      here.parents[2] / "nim_profile_finder_v2.sh"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


FINDER = _find_finder()


def ngc_configured(key: str | None = None) -> bool:
    """True when an NGC key is set (empty values and placeholders such as <NGC_API_KEY> do not count)."""
    key = NGC_API_KEY if key is None else key.strip()
    return bool(key) and not re.fullmatch(r"<.*>|\$\{.*\}|change[-_ ]?me|replace[-_ ]?me|x+", key, re.I)


def config() -> dict:
    tools = {t: bool(shutil.which(t)) for t in TOOLS}
    return {
        "enabled": IMPORT_ENABLED, "ngc_configured": ngc_configured(), "finder": str(FINDER) if FINDER else None,
        "finder_available": bool(FINDER) and all(tools.values()), "tools": tools, "workers": FETCH_WORKERS,
        "timeout_seconds": FETCH_TIMEOUT, "max_file_mb": FETCH_MAX_FILE_MB, "max_bulk": MAX_BULK,
        "internal_registry_prefix": INTERNAL_PREFIX,
    }


# ---------------------------------------------------------------------------
# input: image references and catalog entries
# ---------------------------------------------------------------------------

_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_HOST = r"(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}|localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d{1,5})?"
IMAGE_RE = re.compile(rf"^{_HOST}(?:/{_COMPONENT})+(?::[A-Za-z0-9_][A-Za-z0-9._-]{{0,127}})?$")
NGC_URL_RE = re.compile(r"^https?://catalog\.ngc\.nvidia\.com/orgs/(?P<org>[^/?#]+)(?:/teams/(?P<team>[^/?#]+))?"
                        r"/containers/(?P<repo>[^/?#]+)", re.I)
EXAMPLE_IMAGE = "nvcr.io/nim/meta/llama-3.1-8b-instruct:1.8.4"


def parse_image_input(text: str) -> str:
    """Image reference from an image, a 'docker pull' command, a docker:// or https:// URL or an NGC catalog URL.

    https://catalog.ngc.nvidia.com/orgs/nim/teams/meta/containers/llama-3.1-8b-instruct?version=1.8.4
    -> nvcr.io/nim/meta/llama-3.1-8b-instruct:1.8.4
    """
    s = (text or "").strip().strip("'\"`").strip()
    s = re.sub(r"^(?:sudo\s+)?(?:docker|podman|nerdctl)\s+pull\s+", "", s, flags=re.I)
    m = NGC_URL_RE.match(s)
    if m:
        version = (parse_qs(urlparse(s).query).get("version") or [""])[0].strip()
        s = "nvcr.io/" + "/".join(p for p in (m["org"], m["team"], m["repo"]) if p) + (f":{version}" if version else "")
    else:
        s = re.sub(r"^(?:docker|oci)://", "", s, flags=re.I)
        s = re.sub(r"^https?://", "", s, flags=re.I).rstrip("/")
    if not s:
        raise ValueError(f"enter a NIM image, e.g. {EXAMPLE_IMAGE}")
    if "@" in s:
        raise ValueError("image digests are not supported: use image:tag")
    if re.search(r"\s", s):
        raise ValueError("an image reference cannot contain spaces")
    host, _, rest = s.partition("/")
    path, tag = rest.rsplit(":", 1) if ":" in rest.rsplit("/", 1)[-1] else (rest, "")
    s = f"{host.lower()}/{path.lower()}" + (f":{tag}" if tag else "")
    if len(s) > 400 or not rest or not IMAGE_RE.match(s):
        raise ValueError(f"'{s[:200]}' is not an image reference such as {EXAMPLE_IMAGE}")
    return s


def _text(value: Any, limit: int) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None


def sanitize_entry(raw: dict, image: str) -> dict:
    """Keep the catalog fields the importer uses (nim_catalog.json format), with safe types and sizes."""
    labels: dict[str, list[str]] = {}
    if isinstance(raw.get("labels"), dict):
        for key, values in list(raw["labels"].items())[:50]:
            key = re.sub(r"[^A-Za-z0-9_-]", "_", str(key))[:60]
            values = values if isinstance(values, list) else [values]
            clean = [t for t in (_text(v, 200) for v in values[:100]) if t]
            if key and clean:
                labels[key] = clean
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    metadata = {k: t for k in ("logo", "latestTagPushedDate", "hasSignedTag") if (t := _text(meta.get(k), 2000))}
    if isinstance(meta.get("hasSignedTag"), bool):
        metadata["hasSignedTag"] = "true" if meta["hasSignedTag"] else "false"
    size = raw.get("latestVersionSizeInBytes")
    if isinstance(size, str) and size.strip().isdigit():
        size = int(size.strip())
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or size < 0:
        size = None
    return {
        "name": _text(raw.get("name"), 200), "displayName": _text(raw.get("displayName"), 200),
        "description": _text(raw.get("description"), 4000), "image": image,
        "latestVersionIdStr": _text(raw.get("latestVersionIdStr"), 100), "latestVersionSizeInBytes": size,
        "labels": labels, "metadata": metadata,
    }


LIST_KEYS = ("items", "entries", "nims", "models", "catalog", "images", "data")


def normalize_entries(data: Any) -> tuple[list[dict], list[dict]]:
    """Catalog JSON -> (entries, problems).

    Accepts a list of catalog entries (each with an "image") and/or image strings, an object holding such a list
    under items / entries / nims / models / catalog / images / data, or a single entry.
    """
    if isinstance(data, dict):
        data = [data] if "image" in data else next((data[k] for k in LIST_KEYS if isinstance(data.get(k), list)),
                                                   None)
    if not isinstance(data, list):
        raise ValueError('expected a JSON array of catalog entries with an "image" field (like nim_catalog.json)')
    if not data:
        raise ValueError("the catalog is empty")
    if len(data) > MAX_BULK:
        raise ValueError(f"too many entries: {len(data)} (at most {MAX_BULK} per import)")
    entries, problems = [], []
    for i, raw in enumerate(data):
        if isinstance(raw, str):
            raw = {"image": raw}
        if not isinstance(raw, dict) or not isinstance(raw.get("image"), str):
            problems.append({"index": i, "name": _text(raw.get("name"), 200) if isinstance(raw, dict) else None,
                             "image": None, "message": 'entry has no "image" text'})
            continue
        try:
            image = parse_image_input(raw["image"])
        except ValueError as exc:
            problems.append({"index": i, "name": _text(raw.get("name"), 200), "image": raw["image"][:300],
                             "message": str(exc)})
            continue
        entries.append({"index": i, "entry": sanitize_entry(raw, image)})
    return entries, problems


SUMMARY_FIELDS = {"fetched_image": 1, "profile_count": 1, "display_name": 1, "fetch.status": 1}


def _namespace(fetched_image: str) -> str:
    parts = fetched_image.split("/")
    return "-".join(parts[1:-1]) or parts[0]


def resolve_folders(db, images: list[str]) -> list[tuple[str, str, dict | None]]:
    """(folder, fetched image, stored model) for every image.

    The folder (= model id) follows bulk_fetch_profiles.sh (<repo>_<tag>). When another image already uses it
    (same repository and tag in another NGC team), the image namespace is appended so nothing is overwritten.
    """
    base = [folder_for_image(i) for i in images]
    known = {m["_id"]: m for m in db.models.find({"_id": {"$in": sorted({f for f, _ in base})}}, SUMMARY_FIELDS)}
    out, extra = [], {}
    for folder, fetched in base:
        model = known.get(folder)
        if model and model.get("fetched_image") != fetched:
            folder = re.sub(r"[^A-Za-z0-9._-]", "_", f"{folder}_{_namespace(fetched)}")
            if folder not in extra:
                extra[folder] = db.models.find_one({"_id": folder}, SUMMARY_FIELDS)
            model = extra[folder]
        out.append((folder, fetched, model))
    return out


def classify(db, entries: list[dict], force: bool) -> list[dict]:
    """State of every catalog entry: new, retry (stored without profiles), refresh (force), skip or duplicate."""
    rows, first = [], {}
    resolved = resolve_folders(db, [e["entry"]["image"] for e in entries])
    for e, (folder, fetched, model) in zip(entries, resolved):
        entry = e["entry"]
        count = (model or {}).get("profile_count") or 0
        row = {"index": e["index"], "folder": folder, "image": entry["image"], "fetched_image": fetched,
               "name": entry.get("displayName") or entry.get("name") or (model or {}).get("display_name") or folder,
               "profiles": count if model else None, "entry": entry}
        if folder in first:
            row.update(state="duplicate", message=f"same image as entry {first[folder]}")
        elif not model:
            row.update(state="new", message="not in the database yet")
        elif not count:
            status = (model.get("fetch") or {}).get("status") or "missing"
            row.update(state="retry", message=f"in the database without profiles (fetch {status})")
        elif force:
            row.update(state="refresh", message=f"re-fetch ({count} profiles in the database)")
        else:
            row.update(state="skip", message=f"already in the database with {count} profiles")
        first.setdefault(folder, e["index"])
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# running nim_profile_finder_v2.sh
# ---------------------------------------------------------------------------

@dataclass
class FinderResult:
    ok: bool
    manifest_yaml: str | None
    profiles_reported: int | None
    seconds: float
    message: str | None
    log: str
    cancelled: bool = False


# same transient errors bulk_fetch_profiles.sh retries
TRANSIENT = re.compile(r"timeout|timed out|connection reset|unexpected EOF|TLS handshake|too many requests|"
                       r"429|502|503|504|temporary failure", re.I)
ERROR_LINE = re.compile(r"ERROR|FATA|Error|error")


def summarize_log(text: str, rc: int, max_file_mb: int = FETCH_MAX_FILE_MB) -> str:
    """Short failure reason, as bulk_fetch_profiles.sh writes it into _fetch_status.tsv."""
    if rc == 153 or re.search(r"file size limit", text, re.I):
        return f"layer larger than {max_file_mb} MB (aborted by safety cap)"
    lines = [" ".join(line.split()) for line in text.splitlines() if ERROR_LINE.search(line)]
    return "  ".join(lines[:2])[:300] or f"exit code {rc}"


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def tail(text: str, lines: int = 200, chars: int = 20000) -> str:
    return "\n".join(text.splitlines()[-lines:])[-chars:]


def _kill(proc: subprocess.Popen) -> None:
    """Stop the finder and everything it started (skopeo, curl)."""
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        if proc.poll() is not None:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, sig)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _execute(cmd: list[str], env: dict, cwd: Path, log_path: Path, timeout: int, cancelled: Callable[[], bool],
             on_start: Callable, on_end: Callable) -> tuple[int, str]:
    """Run the finder; returns (exit code, 'ok' | 'cancelled' | 'timeout')."""
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, env=env,
                                cwd=cwd, start_new_session=True)
        on_start(proc)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    rc = proc.wait(timeout=1)
                    # killed by a signal while a stop was requested (website shutdown): not a fetch failure
                    return rc, "cancelled" if rc < 0 and cancelled() else "ok"
                except subprocess.TimeoutExpired:
                    outcome = "cancelled" if cancelled() else "timeout" if time.monotonic() > deadline else None
                    if outcome:
                        _kill(proc)
                        return (proc.returncode if proc.returncode is not None else -9), outcome
        finally:
            on_end(proc)


def _sleep(seconds: float, cancelled: Callable[[], bool]) -> bool:
    """Sleep unless cancelled; returns True when cancelled."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            return True
        time.sleep(0.5)
    return cancelled()


def run_finder(image: str, *, ngc_key: str | None = None, finder: Path | None = None, timeout: int = FETCH_TIMEOUT,
               max_file_mb: int = FETCH_MAX_FILE_MB, attempts: int = 3,
               cancelled: Callable[[], bool] = lambda: False,
               on_start: Callable = lambda proc: None, on_end: Callable = lambda proc: None) -> FinderResult:
    """Run nim_profile_finder_v2.sh for one image with a private HOME holding the NGC credentials."""
    finder = finder or FINDER
    ngc_key = NGC_API_KEY if ngc_key is None else ngc_key
    if not finder:
        raise RuntimeError("nim_profile_finder_v2.sh not found (set FINDER_SCRIPT)")
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="nim-fetch-"))
    secrets: list[str] = []
    try:
        home, out, work = tmp / "home", tmp / "out", tmp / "tmp"
        (home / ".config" / "containers").mkdir(parents=True)
        work.mkdir()
        env = {"PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
               "TMPDIR": str(work), "LANG": "C.UTF-8"}
        env.update({k: os.environ[k] for k in PASS_ENV if os.environ.get(k)})
        if ngc_configured(ngc_key):
            # the finder reads the nvcr.io login from $HOME/.config/containers/auth.json (as `skopeo login` writes it)
            auth = base64.b64encode(f"$oauthtoken:{ngc_key}".encode()).decode()
            secrets = [ngc_key, auth]
            auth_file = home / ".config" / "containers" / "auth.json"
            auth_file.write_text(json.dumps({"auths": {"nvcr.io": {"auth": auth}}}), encoding="utf-8")
            auth_file.chmod(0o600)
        # ulimit -f: the safety cap of bulk_fetch_profiles.sh against huge manifest layers (1 KB blocks)
        cmd = ["bash", "-c", 'ulimit -f "$1" && exec bash "$2" "$3" "$4"', "nim-finder",
               str(max_file_mb * 1024), str(finder), image, str(out)]
        log_path = tmp / "finder.log"
        rc, outcome, text = 1, "ok", ""
        for attempt in range(1, attempts + 1):
            shutil.rmtree(out, ignore_errors=True)
            rc, outcome = _execute(cmd, env, tmp, log_path, timeout, cancelled, on_start, on_end)
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if rc == 0 or outcome != "ok" or attempt == attempts or not TRANSIENT.search(text):
                break
            if _sleep(attempt * 10, cancelled):
                outcome = "cancelled"
                break
        manifest = out / "model_manifest.yaml"
        manifest_yaml = None
        if outcome != "cancelled" and manifest.is_file() and manifest.stat().st_size > 0:
            manifest_yaml = manifest.read_text(encoding="utf-8", errors="replace")
        found = re.findall(r"Profiles found: (\d+)", text)
        text = redact(text, secrets)
        if outcome == "cancelled":
            message = "cancelled"
        elif outcome == "timeout":
            message = f"timed out after {timeout} s"
        else:
            message = None if rc == 0 else summarize_log(text, rc, max_file_mb)
        return FinderResult(ok=rc == 0 and outcome == "ok", manifest_yaml=manifest_yaml,
                            profiles_reported=int(found[-1]) if found else None,
                            seconds=round(time.monotonic() - started, 1), message=message, log=tail(text),
                            cancelled=outcome == "cancelled")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

ACTIVE = ("queued", "running")
RETRYABLE = ("failed", "cancelled", "interrupted")
LIST_FIELDS = {"items": 0, "problems": 0}
DETAIL_FIELDS = {"items.entry": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _counts(items: list[dict]) -> dict:
    return dict(Counter(i["status"] for i in items))


class JobManager:
    """Runs fetch jobs in background threads of the web process. Job state lives in MongoDB (import_jobs), so any
    replica can show or cancel a job; a job whose process died is marked interrupted once its heartbeat stops."""

    def __init__(self, db, on_change: Callable[[], None] = lambda: None):
        self.db = db
        self.on_change = on_change
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._procs: dict[str, set[subprocess.Popen]] = {}
        self._cancelled: set[str] = set()
        self._local: set[str] = set()
        self._stopping = threading.Event()
        self._schema_ready = False

    # ------------------------------------------------------------ creation
    def _executor(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="nim-fetch")
            return self._pool

    def create_single(self, image: str, display_name: str | None = None, description: str | None = None) -> dict:
        folder, fetched, model = resolve_folders(self.db, [image])[0]
        entry = {"image": image, "displayName": _text(display_name, 200), "description": _text(description, 4000)}
        if not model:
            repo, _, tag = fetched.rsplit("/", 1)[-1].partition(":")
            entry["name"] = repo
            entry["displayName"] = entry["displayName"] or f"{repo} ({tag or 'latest'})"
        row = {"folder": folder, "image": image, "fetched_image": fetched, "entry": entry,
               "state": "refresh" if model else "new", "profiles": (model or {}).get("profile_count"),
               "name": entry["displayName"] or (model or {}).get("display_name") or entry.get("name") or folder}
        return self.create_job("single", [row], force=True)

    def create_job(self, kind: str, rows: list[dict], force: bool, problems: list[dict] | None = None,
                   source_name: str | None = None, retry_of: str | None = None) -> dict:
        if not self._schema_ready:
            ensure_schema(self.db)
            self._schema_ready = True
        now = _now()
        items = []
        for i, row in enumerate(rows):
            skipped = row.get("state") in ("skip", "duplicate")
            items.append({
                "index": i, "folder": row["folder"], "name": row.get("name"), "image": row["image"],
                "fetched_image": row["fetched_image"], "status": "skipped" if skipped else "queued", "action": None,
                "profiles": row.get("profiles"), "seconds": None, "message": row.get("message") if skipped else None,
                "entry": row.get("entry") or {"image": row["image"]}, "has_log": False,
            })
        job = {"_id": f"{now:%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:6]}", "kind": kind, "status": "queued",
               "created_at": now, "started_at": None, "finished_at": None, "heartbeat_at": now, "owner": OWNER,
               "force": bool(force), "cancel_requested": False, "total": len(items), "counts": _counts(items),
               "source_name": _text(source_name, 200), "retry_of": retry_of,
               "problems": (problems or [])[:MAX_BULK], "items": items}
        self.db.import_jobs.insert_one(job)
        self._prune()
        with self._lock:
            self._local.add(job["_id"])
        threading.Thread(target=self._run_job, args=(job["_id"],), name=f"job-{job['_id']}", daemon=True).start()
        return {k: v for k, v in job.items() if k not in ("items", "problems")}

    def _prune(self) -> None:
        old = [j["_id"] for j in self.db.import_jobs.find({"status": {"$nin": list(ACTIVE)}}, {"_id": 1})
               .sort("created_at", -1).skip(KEEP_JOBS)]
        if old:
            self.db.import_jobs.delete_many({"_id": {"$in": old}})
            self.db.import_logs.delete_many({"job_id": {"$in": old}})

    # ------------------------------------------------------------ execution
    def _run_job(self, job_id: str) -> None:
        try:
            now = _now()
            self.db.import_jobs.update_one({"_id": job_id},
                                           {"$set": {"status": "running", "started_at": now, "heartbeat_at": now}})
            job = self.db.import_jobs.find_one({"_id": job_id})
            pending = {self._executor().submit(self._run_item, job_id, item)
                       for item in job["items"] if item["status"] == "queued"}
            while pending:
                _, pending = wait(pending, timeout=3)
                self._tick(job_id)
        except Exception:  # noqa: BLE001 - the job is closed below
            log.exception("import job %s failed", job_id)
        finally:
            try:
                self._finish(job_id)
            except Exception:  # noqa: BLE001
                log.exception("cannot close import job %s", job_id)
            with self._lock:
                self._local.discard(job_id)
                self._procs.pop(job_id, None)
                self._cancelled.discard(job_id)

    def _stop_requested(self, job_id: str) -> bool:
        return self._stopping.is_set() or job_id in self._cancelled

    def _tick(self, job_id: str) -> None:
        """Heartbeat, progress counts and cancel requests made through another replica."""
        doc = self.db.import_jobs.find_one({"_id": job_id}, {"items.status": 1, "cancel_requested": 1})
        if not doc:
            return
        if doc.get("cancel_requested"):
            with self._lock:
                self._cancelled.add(job_id)
        self.db.import_jobs.update_one({"_id": job_id},
                                       {"$set": {"heartbeat_at": _now(), "counts": _counts(doc["items"])}})

    def _finish(self, job_id: str) -> None:
        doc = self.db.import_jobs.find_one({"_id": job_id}, {"status": 1, "cancel_requested": 1})
        if not doc or doc["status"] not in ACTIVE:
            return
        if self._stopping.is_set():
            self._close(job_id, "interrupted", "the website stopped while the job was running")
        elif doc.get("cancel_requested"):
            self._close(job_id, "cancelled", "cancelled")
        else:
            self._close(job_id, "done", "not fetched")
        self.on_change()

    def _close(self, job_id: str, status: str, message: str) -> None:
        """Finish a job: items that never ran get the job status (done -> failed)."""
        leftover = "failed" if status == "done" else status
        self.db.import_jobs.update_one(
            {"_id": job_id}, {"$set": {"items.$[i].status": leftover, "items.$[i].message": message}},
            array_filters=[{"i.status": {"$in": list(ACTIVE)}}])
        doc = self.db.import_jobs.find_one({"_id": job_id}, {"items.status": 1})
        if not doc:
            return
        now = _now()
        self.db.import_jobs.update_one(
            {"_id": job_id, "status": {"$in": list(ACTIVE)}},
            {"$set": {"status": status, "finished_at": now, "heartbeat_at": now, "counts": _counts(doc["items"])}})

    def _set_item(self, job_id: str, index: int, **fields: Any) -> None:
        self.db.import_jobs.update_one({"_id": job_id}, {"$set": {f"items.{index}.{k}": v for k, v in fields.items()}})

    def _track(self, job_id: str, proc: subprocess.Popen, running: bool) -> None:
        with self._lock:
            procs = self._procs.setdefault(job_id, set())
            if running:
                procs.add(proc)
            else:
                procs.discard(proc)

    def _run_item(self, job_id: str, item: dict) -> None:
        index = item["index"]
        stopped = "interrupted" if self._stopping.is_set() else "cancelled"
        try:
            if self._stop_requested(job_id):
                self._set_item(job_id, index, status=stopped, message=stopped)
                return
            self._set_item(job_id, index, status="running", message=None)
            result = run_finder(item["fetched_image"], cancelled=lambda: self._stop_requested(job_id),
                                on_start=lambda p: self._track(job_id, p, True),
                                on_end=lambda p: self._track(job_id, p, False))
            if result.log:
                self.db.import_logs.replace_one(
                    {"_id": f"{job_id}/{index}"},
                    {"_id": f"{job_id}/{index}", "job_id": job_id, "index": index, "log": result.log,
                     "created_at": _now()}, upsert=True)
            if result.cancelled or self._stopping.is_set():
                status = "interrupted" if self._stopping.is_set() else "cancelled"
                self._set_item(job_id, index, status=status, message=status, seconds=result.seconds,
                               has_log=bool(result.log))
                return
            self._set_item(job_id, index, has_log=bool(result.log), **self._store(job_id, item, result))
            self.on_change()
        except Exception as exc:  # noqa: BLE001 - reported on the item
            log.exception("fetching %s failed", item.get("image"))
            self._set_item(job_id, index, status="failed", message=f"internal error: {exc}"[:300])

    def _store(self, job_id: str, item: dict, r: FinderResult) -> dict:
        """Save the fetch result in catalog_additions and rebuild the model; returns the item fields."""
        db, folder, now = self.db, item["folder"], _now()
        stored = db.models.find_one({"_id": folder}, {"profile_count": 1})
        base = {"image": item["image"], "fetched_image": item["fetched_image"],
                "entry": item.get("entry") or {"image": item["image"]}, "source": "website", "job_id": job_id,
                "updated_at": now, "fetch": {"status": "ok" if r.ok else "failed", "message": r.message,
                                             "seconds": r.seconds, "profiles_reported": r.profiles_reported}}
        if r.manifest_yaml:
            db.catalog_additions.update_one(
                {"_id": folder}, {"$set": {**base, "manifest_yaml": r.manifest_yaml, "fetched_at": now},
                                  "$setOnInsert": {"created_at": now}}, upsert=True)
        elif stored and stored.get("profile_count"):
            # a failed refresh never removes working profiles
            count = stored["profile_count"]
            return {"status": "failed", "action": "kept", "profiles": count, "seconds": r.seconds,
                    "message": f"{r.message}; kept the {count} profiles already in the database"}
        else:
            db.catalog_additions.update_one(
                {"_id": folder}, {"$set": base,
                                  "$setOnInsert": {"created_at": now, "manifest_yaml": None, "fetched_at": None}},
                upsert=True)
        result = apply_model(db, folder, job_id=job_id)
        model = result["model"]
        count = model["profile_count"]
        message = model["fetch"].get("message") or (None if count else "the manifest has no usable profiles")
        return {"status": "ok" if count else "failed", "action": result["action"], "profiles": count,
                "seconds": r.seconds, "message": message}

    # ------------------------------------------------------------ queries and control
    def sweep(self) -> None:
        """Jobs of a website process that stopped (no heartbeat) -> interrupted."""
        stale = _now() - STALE_AFTER
        for job in self.db.import_jobs.find({"status": {"$in": list(ACTIVE)}, "heartbeat_at": {"$lt": stale}},
                                            {"_id": 1}):
            with self._lock:
                local = job["_id"] in self._local
            if not local:
                self._close(job["_id"], "interrupted", "the website stopped while the job was running")

    def list_jobs(self, limit: int = 20) -> list[dict]:
        self.sweep()
        return list(self.db.import_jobs.find({}, LIST_FIELDS).sort("created_at", -1).limit(limit))

    def get_job(self, job_id: str) -> dict | None:
        self.sweep()
        return self.db.import_jobs.find_one({"_id": job_id}, DETAIL_FIELDS)

    def item_log(self, job_id: str, index: int) -> str | None:
        doc = self.db.import_logs.find_one({"_id": f"{job_id}/{index}"}, {"log": 1})
        return doc["log"] if doc else None

    def cancel(self, job_id: str) -> bool:
        res = self.db.import_jobs.update_one({"_id": job_id, "status": {"$in": list(ACTIVE)}},
                                             {"$set": {"cancel_requested": True}})
        if res.matched_count:
            with self._lock:
                if job_id in self._local:
                    self._cancelled.add(job_id)
        return bool(res.matched_count)

    def retry(self, job_id: str) -> dict | None:
        """New job for the failed, cancelled and interrupted images of a finished job."""
        job = self.db.import_jobs.find_one({"_id": job_id})
        if not job or job["status"] in ACTIVE:
            return None
        rows = [{"folder": it["folder"], "image": it["image"], "fetched_image": it["fetched_image"],
                 "name": it.get("name"), "entry": it.get("entry"), "profiles": it.get("profiles"), "state": "refresh"}
                for it in job["items"] if it["status"] in RETRYABLE]
        if not rows:
            return None
        return self.create_job(job["kind"], rows, force=True, source_name=job.get("source_name"), retry_of=job_id)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop running finders and mark this process' jobs interrupted (website shutdown)."""
        self._stopping.set()
        with self._lock:
            procs = [p for group in self._procs.values() for p in group]
            local = list(self._local)
            pool = self._pool
        for proc in procs:
            _kill(proc)
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._local:
                    break
            time.sleep(0.2)
        for job_id in local:
            try:
                self._close(job_id, "interrupted", "the website stopped while the job was running")
            except Exception:  # noqa: BLE001 - best effort during shutdown
                pass
