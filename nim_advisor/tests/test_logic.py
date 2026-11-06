"""Unit tests for manifest normalization and the recommendation engine (no MongoDB needed).

Run inside the image:
  docker compose run --rm -v ./nim_advisor/tests:/srv/tests --entrypoint python importer -m unittest discover -s tests -v
"""

import unittest

from app.normalize import (
    folder_for_image, iter_profiles, load_manifest, norm_name, normalize_profile, parse_compute_capability,
    parse_device_ids,
)
from app.recommender import Context, Store, evaluate, rank_key, recommend, resolve_gpu


def gpu(gid, name, ids, cc, vram, family, arch="Hopper", aliases=(), internal=()):
    al = [name, gid, *aliases, *internal]
    return {"_id": gid, "name": name, "family": family, "architecture": arch, "compute_capability": cc,
            "vram_gb": vram, "pci_device_ids": ids, "nim_gpu_device": [f"{i}:10de" for i in ids],
            "aliases": al, "aliases_norm": sorted({norm_name(a) for a in al}), "internal_names": list(internal),
            "k8s_gpu_product": [], "fp8": True, "nvfp4": False, "in_pcai": True}


GPUS = {
    "h200-nvl": gpu("h200-nvl", "NVIDIA H200 NVL", ["233b"], "9.0", 141, "H200", internal=["h200-nvl", "h200_nvl"]),
    "h200-sxm": gpu("h200-sxm", "NVIDIA H200 SXM", ["2335"], "9.0", 141, "H200", internal=["h200"]),
    "l40s": gpu("l40s", "NVIDIA L40S", ["26b9"], "8.9", 48, "L40S", arch="Ada Lovelace", internal=["l40s"]),
    "a10g": gpu("a10g", "NVIDIA A10G", ["2237"], "8.6", 24, "A10G", arch="Ampere", internal=["a10g"]),
}


class FakeStore(Store):
    def __init__(self, models, profiles):
        super().__init__(db=None)
        self.gpus = GPUS
        self.models = models
        self.profiles = profiles
        self.pcai = []
        self.validation = {}
        self._run_id = "test"

    def refresh(self, force=False):
        return None


def prof(model, pid, **tags):
    p = normalize_profile({"id": pid, "tags": tags})
    p.update({"_id": f"{model}/{pid}", "model_id": model})
    ids = []
    for d in p["hw"]["gpu_device_ids"]:
        ids += [g["_id"] for g in GPUS.values() if d in g["pci_device_ids"]]
    for n in p["hw"]["gpu_names"]:
        ids += [g["_id"] for g in GPUS.values() if norm_name(n) in g["aliases_norm"]]
    p["hw"]["gpu_ids"] = list(dict.fromkeys(ids))
    return p


def model(mid, name=None):
    return {"_id": mid, "name": name or mid, "display_name": name or mid, "category": "LLM / VLM", "image": f"nvcr.io/nim/x/{mid}",
            "fetched_image": f"nvcr.io/nim/x/{mid}", "profile_count": 0, "fetch": {"status": "ok"}, "manifest": {}}


class NormalizeTests(unittest.TestCase):
    def test_device_ids(self):
        self.assertEqual(parse_device_ids("2335:10de"), (["2335"], []))
        self.assertEqual(parse_device_ids("20B2"), (["20b2"], []))
        self.assertEqual(parse_device_ids("2335:10de, 233b:10de"), (["2335", "233b"], []))
        self.assertEqual(parse_device_ids("zz"), ([], ["zz"]))

    def test_gpu_count_and_vram(self):
        p = normalize_profile({"id": "x", "tags": {"tp": "4", "pp": "2", "number_of_nodes": "2", "vram": "20 GiB"}})
        self.assertEqual((p["gpus_required"], p["nodes_required"], p["gpus_per_node_required"]), (8, 2, 4))
        self.assertAlmostEqual(p["min_vram_gb"], 21.5, places=1)
        p = normalize_profile({"id": "y", "tags": {"tp": "4", "n_gpus": "4", "nim_tp": "1"}})
        self.assertEqual(p["gpus_required"], 4)

    def test_match_modes(self):
        self.assertEqual(normalize_profile({"id": "a", "tags": {"gpu_device": "2335:10de", "gpu": "h200"}})["hw"]["match_mode"], "gpu_device")
        self.assertEqual(normalize_profile({"id": "b", "tags": {"gpu": "a10g"}})["hw"]["match_mode"], "gpu_name")
        self.assertEqual(normalize_profile({"id": "c", "tags": {"compute_capability": "8.9"}})["hw"]["match_mode"], "compute_capability")
        self.assertEqual(normalize_profile({"id": "d", "tags": {"gpu": "generic"}})["hw"]["match_mode"], "generic")

    def test_manifest_formats(self):
        f1 = load_manifest("schema_version: '2.1'\nprofiles:\n- id: abc\n  tags: {tp: '1'}\n")
        self.assertEqual([p["id"] for p in iter_profiles(f1)], ["abc"])
        hexid = "a" * 64
        f2 = load_manifest(f"{hexid}:\n  model: m\n  tags: {{tp: '2'}}\n  workspace: !workspace\n    components: []\n")
        self.assertEqual([p["id"] for p in iter_profiles(f2)], [hexid])
        f3 = load_manifest("model_profile: X\nmanifest:\n  - id: X\n    tags: {backend: pytorch}\n")
        self.assertEqual([p["id"] for p in iter_profiles(f3)], ["X"])

    def test_precision_alias(self):
        self.assertEqual(normalize_profile({"id": "a", "tags": {"precision": "bfloat16"}})["precision"], "bf16")

    def test_folder_names(self):
        self.assertEqual(folder_for_image("nvcr.io/nim/meta/llama-3.1-8b-instruct:latest"),
                         ("llama-3.1-8b-instruct_latest", "nvcr.io/nim/meta/llama-3.1-8b-instruct:latest"))
        self.assertEqual(folder_for_image("registry.example.com/ezmeral-common/nvcr.io/nim/meta/x-pb25h1:1.10")[0],
                         "x-pb25h1_1.10_ezmeral-common")
        self.assertEqual(folder_for_image("nvcr.io/nim/nvidia/foo")[1], "nvcr.io/nim/nvidia/foo:latest")

    def test_device_id_and_gpu_key_variants(self):
        # llama-3.2-nemoretriever-500m-rerank-v2 / paddleocr use device_id + gpu_key instead of gpu_device + gpu
        p = normalize_profile({"id": "x", "tags": {"device_id": "26b9:10de", "gpu_key": "l40s", "gpu": "NVIDIA L40S"}})
        self.assertEqual(p["hw"]["match_mode"], "gpu_device")
        self.assertEqual(p["hw"]["gpu_device_ids"], ["26b9"])
        self.assertEqual(p["hw"]["gpu_names"], ["NVIDIA L40S", "l40s"])

    def test_sm_version_and_nnodes(self):
        p = normalize_profile({"id": "y", "tags": {"gpu_sm_version": "sm_100", "tp": "1", "nnodes": "2"}})
        self.assertEqual(p["hw"]["compute_capability"], "10.0")
        self.assertEqual(p["hw"]["match_mode"], "compute_capability")
        self.assertEqual((p["nodes_required"], p["gpus_required"], p["gpus_per_node_required"]), (2, 2, 1))
        self.assertEqual(parse_compute_capability({"gpu_sm_version": "sm_89"}), "8.9")
        self.assertEqual(parse_compute_capability({"gpu_sm_version": "sm_120"}), "12.0")
        self.assertEqual(parse_compute_capability({"gpu_cc": "7.5"}), "7.5")


class RecommenderTests(unittest.TestCase):
    def setUp(self):
        self.models = {"llama-70b": model("llama-70b", "llama-3.1-70b-instruct"), "emb": model("emb", "embedder")}
        self.profiles = {
            "llama-70b": [
                prof("llama-70b", "sxm-fp8-tp2", gpu_device="2335:10de", gpu="h200", tp="2", pp="1", precision="fp8",
                     llm_engine="tensorrt_llm", profile="throughput"),
                prof("llama-70b", "nvl-fp8-tp2", gpu_device="233b:10de", gpu="h200-nvl", tp="2", pp="1", precision="fp8",
                     llm_engine="tensorrt_llm", profile="throughput"),
                prof("llama-70b", "gen-bf16-tp1", tp="1", pp="1", precision="bf16", llm_engine="vllm"),
                prof("llama-70b", "gen-bf16-tp4", tp="4", pp="1", precision="bf16", llm_engine="vllm"),
                prof("llama-70b", "gen-fp8-tp8", tp="8", pp="1", precision="fp8", llm_engine="vllm"),
            ],
            "emb": [prof("emb", "a10g-trt", gpu="a10g", model_type="tensorrt", precision="fp16"),
                    prof("emb", "onnx", model_type="onnx", precision="fp16")],
        }
        self.store = FakeStore(self.models, self.profiles)

    def ctx(self, gid, count, goal="nim", vram=None):
        g = GPUS[gid]
        return Context(gpu=g, count=count, nodes=1, vram_gb=vram or g["vram_gb"], mig=None, goal=goal, lora=False)

    def test_exact_device_wins(self):
        r = recommend(self.store, "h200-nvl", 2)
        best = next(x for x in r["results"] if x["model"]["id"] == "llama-70b")["best"]
        self.assertEqual(best["profile_id"], "nvl-fp8-tp2")

    def test_sibling_not_auto_selected(self):
        ev = evaluate(self.store, self.models["llama-70b"], self.profiles["llama-70b"][0], self.ctx("h200-nvl", 2))
        self.assertFalse(ev["compatible"])
        self.assertTrue(ev["sibling"])
        self.assertEqual(ev["sibling_reason"], "family")

    def test_vram_estimate_blocks_70b_bf16_on_one_l40s(self):
        ev = evaluate(self.store, self.models["llama-70b"], self.profiles["llama-70b"][2], self.ctx("l40s", 8))
        self.assertFalse(ev["compatible"])  # ~154 GB of bf16 weights on one 48 GB GPU
        ev4 = evaluate(self.store, self.models["llama-70b"], self.profiles["llama-70b"][3], self.ctx("l40s", 4))
        self.assertTrue(ev4["compatible"])  # tp=4 -> ~38.5 GB per GPU

    def test_gpu_count_blocks(self):
        ev = evaluate(self.store, self.models["llama-70b"], self.profiles["llama-70b"][4], self.ctx("h200-nvl", 4))
        self.assertFalse(ev["compatible"])

    def test_precision_architecture(self):
        ev = evaluate(self.store, self.models["llama-70b"], self.profiles["llama-70b"][4], self.ctx("a10g", 8))
        self.assertTrue(any("fp8 needs compute capability" in b for b in ev["blockers"]))

    def test_name_match_for_non_llm(self):
        r = recommend(self.store, "A10G", 1)
        best = next(x for x in r["results"] if x["model"]["id"] == "emb")["best"]
        self.assertEqual(best["profile_id"], "a10g-trt")
        r = recommend(self.store, "l40s", 1)
        best = next(x for x in r["results"] if x["model"]["id"] == "emb")["best"]
        self.assertEqual(best["profile_id"], "onnx")

    def test_resolve_by_pci_and_label(self):
        self.assertEqual(resolve_gpu(self.store, "233b")[0]["_id"], "h200-nvl")
        self.assertEqual(resolve_gpu(self.store, "233b:10de")[0]["_id"], "h200-nvl")
        self.assertEqual(resolve_gpu(self.store, "NVIDIA-H200-NVL")[0]["_id"], "h200-nvl")

    def test_family_query_offers_choices(self):
        # 'H200' is a NIM tag for the SXM part, but on PCAI it usually means the H200 NVL: ask instead of guessing
        self.assertEqual({g["_id"] for g in resolve_gpu(self.store, "H200")}, {"h200-nvl", "h200-sxm"})
        r = recommend(self.store, "H200", 1)
        self.assertIn("error", r)
        self.assertEqual(len(r["gpu_candidates"]), 2)

    def test_name_list_with_cc_accepts_same_cc_gpu(self):
        # Maxine-style engine tested on L4 / L40 / RTX4090 and built for CC 8.9: an L40S (8.9) can run it
        p = prof("emb", "ada-trt", gpu="L4, L40, RTX4090", gpu_cc="8.9", model_type="tensorrt", precision="fp16")
        ev = evaluate(self.store, self.models["emb"], p, self.ctx("l40s", 1))
        self.assertTrue(ev["compatible"])
        self.assertEqual(ev["match"], "compute_capability")
        self.assertTrue(ev["warnings"])
        self.assertFalse(evaluate(self.store, self.models["emb"], p, self.ctx("h200-nvl", 1))["compatible"])

    def test_manifest_error_profile_is_forceable(self):
        # older LLM NIMs tag 'gpu: L40S' with gpu_device 26b5 (the L40 id): not auto-selected on an L40S
        p = prof("llama-70b", "mislabeled", gpu_device="26b5:10de", gpu="L40S", tp="4", pp="1", precision="fp16",
                 llm_engine="tensorrt_llm")
        p["hw"].update(gpu_ids=[], name_gpu_ids=["l40s"])
        ev = evaluate(self.store, self.models["llama-70b"], p, self.ctx("l40s", 4))
        self.assertFalse(ev["compatible"])
        self.assertTrue(ev["sibling"])
        self.assertEqual(ev["sibling_reason"], "name")
        self.assertIn("manifest error", ev["blockers"][0])

    def test_goals_change_ranking(self):
        c = self.ctx("h200-nvl", 8, goal="min_gpus")
        ok = [p for p in self.profiles["llama-70b"] if evaluate(self.store, self.models["llama-70b"], p, c)["compatible"]]
        ok.sort(key=lambda p: rank_key(p, evaluate(self.store, self.models["llama-70b"], p, c), c), reverse=True)
        # bf16 tp=1 is blocked by the weight-memory estimate (~154 GB > 141 GB), so tp=2 is the smallest fit
        self.assertEqual(ok[0]["profile_id"], "nvl-fp8-tp2")
        c = self.ctx("h200-nvl", 8, goal="latency")
        ok.sort(key=lambda p: rank_key(p, evaluate(self.store, self.models["llama-70b"], p, c), c), reverse=True)
        self.assertEqual(ok[0]["profile_id"], "nvl-fp8-tp2")  # exact hardware match still wins


if __name__ == "__main__":
    unittest.main()
