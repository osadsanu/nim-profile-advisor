"""Unit tests for the Load NIMs feature: input parsing, catalog merge and the finder wrapper (no MongoDB needed).

Run inside the image:
  docker compose run --rm -v ./nim_advisor/tests:/srv/tests:ro --entrypoint python importer -m unittest discover -s tests -v
"""

import os
import shutil
import signal
import tempfile
import threading
import unittest
from pathlib import Path

from app.fetcher import (
    MAX_BULK, classify, ngc_configured, normalize_entries, parse_image_input, redact, run_finder, sanitize_entry,
    summarize_log,
)
from app.importer import build_model, entry_from_model, merge_catalog, unique_name

MANIFEST = """schema_version: '2.1'
profiles:
- id: 1111111111111111111111111111111111111111111111111111111111111111
  tags: {llm_engine: tensorrt_llm, precision: fp8, tp: '2', pp: '1', gpu: H200-NVL, gpu_device: 233b:10de}
- id: 2222222222222222222222222222222222222222222222222222222222222222
  tags: {llm_engine: vllm, precision: bf16, tp: '1', pp: '1'}
"""


class FakeModels:
    def __init__(self, docs):
        self.docs = {d["_id"]: d for d in docs}

    def find(self, query, projection=None):
        return [self.docs[i] for i in query["_id"]["$in"] if i in self.docs]

    def find_one(self, query, projection=None):
        return self.docs.get(query["_id"])


class FakeDB:
    def __init__(self, models):
        self.models = FakeModels(models)


class ImageInputTests(unittest.TestCase):
    def test_accepted_forms(self):
        image = "nvcr.io/nim/meta/llama-3.1-8b-instruct:1.8.4"
        for text in (image, f"  docker pull {image} ", f"docker://{image}", f"https://{image}", f"'{image}'",
                     "https://catalog.ngc.nvidia.com/orgs/nim/teams/meta/containers/llama-3.1-8b-instruct?version=1.8.4"):
            self.assertEqual(parse_image_input(text), image, text)

    def test_ngc_urls(self):
        self.assertEqual(parse_image_input("https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/containers/foo/tags"),
                         "nvcr.io/nim/nvidia/foo")
        self.assertEqual(parse_image_input(
            "https://catalog.ngc.nvidia.com/orgs/nvidia/containers/tritonserver?version=24.01-py3"),
            "nvcr.io/nvidia/tritonserver:24.01-py3")

    def test_case_and_mirror(self):
        self.assertEqual(parse_image_input("NVCR.IO/Nim/Meta/Llama:Latest-ABC"), "nvcr.io/nim/meta/llama:Latest-ABC")
        mirror = "registry.example.com/ezmeral-common/nvcr.io/nim/meta/x-pb25h1:1.10"
        self.assertEqual(parse_image_input(mirror), mirror)
        self.assertEqual(parse_image_input("registry.local:5000/team/app:1"), "registry.local:5000/team/app:1")

    def test_rejected(self):
        for text in ("", "llama", "nvcr.io", "nvcr.io/nim/meta/x@sha256:" + "a" * 64, "nvcr.io/nim/x y",
                     "nvcr.io/nim/-bad:1", "nvcr.io/nim/x:bad tag", "nvcr.io/nim/x:-tag", "; rm -rf /"):
            with self.assertRaises(ValueError, msg=text):
                parse_image_input(text)


class CatalogEntryTests(unittest.TestCase):
    def test_list_forms(self):
        entries, problems = normalize_entries([
            "nvcr.io/nim/meta/a:1",
            {"name": "b", "displayName": "B", "image": "nvcr.io/nim/nvidia/b:2"},
            {"name": "no-image"},
            {"image": "not an image"},
            42,
        ])
        self.assertEqual([e["index"] for e in entries], [0, 1])
        self.assertEqual([p["index"] for p in problems], [2, 3, 4])
        self.assertEqual(entries[1]["entry"]["displayName"], "B")
        self.assertEqual(normalize_entries({"items": ["nvcr.io/nim/meta/a:1"]})[0][0]["entry"]["image"],
                         "nvcr.io/nim/meta/a:1")
        self.assertEqual(len(normalize_entries({"image": "nvcr.io/nim/meta/a:1"})[0]), 1)

    def test_bad_catalogs(self):
        for data in ("text", {"foo": 1}, [], ["nvcr.io/nim/meta/a:1"] * (MAX_BULK + 1)):
            with self.assertRaises(ValueError):
                normalize_entries(data)

    def test_sanitize(self):
        e = sanitize_entry({"name": " x ", "labels": {"general": ["H100", 3, None], "$bad.key": "v", "empty": []},
                            "metadata": {"logo": "https://x/logo.png", "hasSignedTag": True, "other": "drop"},
                            "latestVersionSizeInBytes": "123", "latestVersionIdStr": 1.5}, "nvcr.io/nim/a/b:1")
        self.assertEqual(e["name"], "x")
        self.assertEqual(e["labels"], {"general": ["H100", "3"], "_bad_key": ["v"]})
        self.assertEqual(e["metadata"], {"logo": "https://x/logo.png", "hasSignedTag": "true"})
        self.assertEqual((e["latestVersionSizeInBytes"], e["latestVersionIdStr"]), (123, "1.5"))
        self.assertIsNone(sanitize_entry({"latestVersionSizeInBytes": float("nan")}, "nvcr.io/a/b")
                          ["latestVersionSizeInBytes"])

    def test_classify(self):
        db = FakeDB([
            {"_id": "a_1", "fetched_image": "nvcr.io/nim/meta/a:1", "profile_count": 5},
            {"_id": "b_1", "fetched_image": "nvcr.io/nim/meta/b:1", "profile_count": 0, "fetch": {"status": "failed"}},
        ])
        entries, _ = normalize_entries(["nvcr.io/nim/meta/a:1", "nvcr.io/nim/meta/b:1", "nvcr.io/nim/meta/c:1",
                                        "nvcr.io/nim/meta/c:1", "nvcr.io/nim/nvidia/a:1"])
        rows = classify(db, entries, force=False)
        self.assertEqual([r["state"] for r in rows], ["skip", "retry", "new", "duplicate", "new"])
        # same <repo>_<tag> as a stored image of another team: the namespace keeps them apart
        self.assertEqual(rows[4]["folder"], "a_1_nim-nvidia")
        self.assertEqual(classify(db, entries[:1], force=True)[0]["state"], "refresh")


class MergeTests(unittest.TestCase):
    def test_merge_catalog(self):
        catalog = [{"name": "a", "displayName": "A", "image": "nvcr.io/nim/meta/a:1", "labels": {"general": ["L40S"]}},
                   {"name": "b", "image": "nvcr.io/nim/meta/b"}]
        additions = [
            {"_id": "a_1", "image": "nvcr.io/nim/meta/a:1", "fetched_image": "nvcr.io/nim/meta/a:1",
             "entry": {"image": "nvcr.io/nim/meta/a:1", "displayName": "A refreshed", "name": None}},
            {"_id": "b_latest", "image": "nvcr.io/other/b", "fetched_image": "nvcr.io/other/b:latest", "entry": {}},
            {"_id": "c_2", "image": "nvcr.io/nim/nvidia/c:2", "fetched_image": "nvcr.io/nim/nvidia/c:2",
             "entry": {"name": "c", "image": "nvcr.io/nim/nvidia/c:2"}},
        ]
        warnings = []
        items = merge_catalog(catalog, additions, warnings)
        self.assertEqual([i["folder"] for i in items], ["a_1", "b_latest", "c_2"])
        self.assertEqual(items[0]["entry"]["displayName"], "A refreshed")
        self.assertEqual(items[0]["entry"]["name"], "a")              # empty fields of the addition do not override
        self.assertEqual(items[0]["entry"]["labels"], {"general": ["L40S"]})
        self.assertIsNone(items[1]["addition"])                       # conflicting image is not merged
        self.assertEqual(len(warnings), 1)
        self.assertEqual(items[2]["entry"]["name"], "c")

    def test_unique_name(self):
        used = {"llama"}
        self.assertEqual(unique_name("llama", "llama_1.0", used), "llama_1.0")
        self.assertEqual(unique_name("llama", "llama_1.0", used), "llama_1.0-2")
        self.assertEqual(unique_name("other", "other_1", used), "other")

    def test_build_model(self):
        entry = {"name": "llama-x", "image": "nvcr.io/nim/meta/llama-x:2.0", "labels": {"publisher": ["NVIDIA"]},
                 "metadata": {"logo": "https://x/l.png", "hasSignedTag": "true"}, "latestVersionSizeInBytes": 10}
        warnings = []
        model, profiles = build_model(entry, "llama-x_2.0", "nvcr.io/nim/meta/llama-x:2.0", MANIFEST,
                                      {"status": "ok"}, {"233b": ["h200-nvl"]}, {}, warnings, origin="website")
        self.assertEqual((model["profile_count"], model["origin"], model["tag"], model["fetch"]["status"]),
                         (2, "website", "2.0", "ok"))
        self.assertEqual((model["publisher"], model["signed"], model["category"]), ("NVIDIA", True, "LLM / VLM"))
        self.assertEqual(profiles[0]["_id"], "llama-x_2.0/" + "1" * 64)
        self.assertEqual(profiles[0]["hw"]["gpu_ids"], ["h200-nvl"])
        self.assertFalse(warnings)
        # rebuilding from the stored document keeps the catalog metadata
        again, _ = build_model(entry_from_model(model), "llama-x_2.0", model["fetched_image"], MANIFEST,
                               {"status": "ok"}, {"233b": ["h200-nvl"]}, {}, warnings, origin="website")
        for key in ("name", "display_name", "labels", "logo", "signed", "size_bytes", "publisher", "category"):
            self.assertEqual(again[key], model[key], key)
        failed, _ = build_model(entry, "f", "nvcr.io/nim/meta/f:1", MANIFEST, {"status": "failed", "message": "x"},
                                {}, {}, warnings)
        self.assertEqual(failed["fetch"]["status"], "ok")             # profiles parsed although the finder failed
        missing, _ = build_model(entry, "m", "nvcr.io/nim/meta/m:1", None, {"status": "failed", "message": "denied"},
                                 {}, {}, warnings)
        self.assertEqual((missing["fetch"]["status"], missing["fetch"]["message"], missing["profile_count"]),
                         ("failed", "denied", 0))


class FinderHelperTests(unittest.TestCase):
    def test_summarize(self):
        log = "Using Skopeo auth file:\nERROR: Skopeo cannot access:\n  nvcr.io/x\nError: denied\nmore error"
        self.assertEqual(summarize_log(log, 1), "ERROR: Skopeo cannot access:  Error: denied")
        self.assertIn("safety cap", summarize_log("", 153, 1024))
        self.assertEqual(summarize_log("nothing useful", 2), "exit code 2")

    def test_redact_and_key(self):
        self.assertEqual(redact("key nvapi-123 and token abc", ["nvapi-123", "abc", ""]), "key *** and token ***")
        self.assertTrue(ngc_configured("nvapi-abcdef"))
        for placeholder in ("", "<NGC_API_KEY>", "${NGC_API_KEY}", "change-me", "REPLACE_ME", "xxxx"):
            self.assertFalse(ngc_configured(placeholder), placeholder)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "needs bash (runs in the image / CI)")
class RunFinderTests(unittest.TestCase):
    """run_finder with fake finder scripts: arguments, NGC credentials, outputs, failures, limits."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def script(self, body: str) -> Path:
        path = self.dir / "finder.sh"
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\nIMAGE=$1\nOUT=$2\n" + body, encoding="utf-8")
        return path

    def test_success_with_credentials(self):
        finder = self.script(
            'mkdir -p "$OUT"\n'
            'jq -r \'.auths["nvcr.io"].auth\' "$HOME/.config/containers/auth.json" | base64 -d\n'
            'echo; echo "image=$IMAGE"\n'
            "cat > \"$OUT/model_manifest.yaml\" <<'EOF'\nprofiles:\n- id: abc\n  tags: {tp: '1'}\nEOF\n"
            'echo "Profiles found: 1"\n')
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="nvapi-secret", finder=finder, timeout=30)
        self.assertTrue(r.ok, r.log)
        self.assertIn("id: abc", r.manifest_yaml)
        self.assertEqual(r.profiles_reported, 1)
        self.assertIn("$oauthtoken:***", r.log)                       # the key never reaches the stored log
        self.assertNotIn("nvapi-secret", r.log)
        self.assertIn("image=nvcr.io/nim/meta/x:1", r.log)

    def test_failure_message(self):
        finder = self.script('echo "ERROR: Could not find model_manifest.yaml in image history."\nexit 1\n')
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=30, attempts=1)
        self.assertFalse(r.ok)
        self.assertIsNone(r.manifest_yaml)
        self.assertEqual(r.message, "ERROR: Could not find model_manifest.yaml in image history.")

    def test_manifest_kept_when_profile_regex_fails(self):
        finder = self.script('mkdir -p "$OUT"\necho "profiles: []" > "$OUT/model_manifest.yaml"\n'
                             'echo "ERROR: No NIM profile IDs found in:"\nexit 1\n')
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=30, attempts=1)
        self.assertFalse(r.ok)
        self.assertEqual(r.manifest_yaml.strip(), "profiles: []")

    def test_timeout_and_cancel(self):
        finder = self.script("sleep 30\n")
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=1, attempts=1)
        self.assertEqual(r.message, "timed out after 1 s")
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=30, cancelled=lambda: True)
        self.assertTrue(r.cancelled)
        self.assertLess(r.seconds, 15)

    def test_killed_while_stopping_is_cancelled(self):
        # website shutdown kills the running finders: the image is interrupted, not failed
        finder = self.script("sleep 30\n")
        stop = threading.Event()

        def shutdown(proc):
            stop.set()
            os.killpg(proc.pid, signal.SIGTERM)

        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=30, attempts=1,
                       cancelled=stop.is_set, on_start=shutdown)
        self.assertTrue(r.cancelled)
        self.assertIsNone(r.manifest_yaml)

    def test_file_size_cap(self):
        finder = self.script('mkdir -p "$OUT"\nhead -c 3000000 /dev/zero > "$OUT/layer"\n')
        r = run_finder("nvcr.io/nim/meta/x:1", ngc_key="", finder=finder, timeout=30, max_file_mb=1, attempts=1)
        self.assertFalse(r.ok)
        self.assertEqual(r.message, "layer larger than 1 MB (aborted by safety cap)")


if __name__ == "__main__":
    unittest.main()
