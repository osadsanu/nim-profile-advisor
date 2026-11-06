"""Collect evidence for validating NIM GPU identifiers.

For every gpu_device id, internal gpu name and compute capability found in
nims_catalog/*/model_manifest.yaml this prints (JSON):
  * how many profiles / NIMs use it
  * which other identifiers appear in the same profiles (co-occurrence)
  * the device name from a pci.ids database (optional)

Usage:
  python tools/gpu_evidence.py <nims_catalog dir> [--pci-ids /usr/share/misc/pci.ids] > evidence.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.normalize import iter_profiles, load_manifest, normalize_profile  # noqa: E402


def read_pci_ids(path: Path) -> dict[str, str]:
    """Return {device_id: name} for vendor 10de from a pci.ids file."""
    names, in_nvidia = {}, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith("\t"):
            in_nvidia = line.lower().startswith("10de ")
            continue
        if in_nvidia and not line.startswith("\t\t"):
            m = re.match(r"\t([0-9a-fA-F]{4})\s+(.*)", line)
            if m:
                names[m.group(1).lower()] = m.group(2).strip()
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog_dir")
    ap.add_argument("--pci-ids", default=None)
    args = ap.parse_args()

    pci = read_pci_ids(Path(args.pci_ids)) if args.pci_ids and Path(args.pci_ids).exists() else {}
    dev = defaultdict(lambda: {"profiles": 0, "models": set(), "names": Counter(), "cc": Counter()})
    names = defaultdict(lambda: {"profiles": 0, "models": set(), "devices": Counter(), "cc": Counter()})
    ccs = defaultdict(lambda: {"profiles": 0, "models": set(), "names": Counter()})

    for mf in sorted(Path(args.catalog_dir).glob("*/model_manifest.yaml")):
        model = mf.parent.name
        try:
            profiles = [normalize_profile(p) for p in iter_profiles(load_manifest(mf.read_text(encoding="utf-8")))]
        except Exception as exc:  # noqa: BLE001
            print(f"skip {model}: {exc}", file=sys.stderr)
            continue
        for p in profiles:
            hw = p["hw"]
            cc = hw["compute_capability"]
            for d in hw["gpu_device_ids"]:
                dev[d]["profiles"] += 1
                dev[d]["models"].add(model)
                dev[d]["names"].update(hw["gpu_names"])
                if cc:
                    dev[d]["cc"][cc] += 1
            for n in hw["gpu_names"]:
                names[n]["profiles"] += 1
                names[n]["models"].add(model)
                names[n]["devices"].update(hw["gpu_device_ids"])
                if cc:
                    names[n]["cc"][cc] += 1
            if cc:
                ccs[cc]["profiles"] += 1
                ccs[cc]["models"].add(model)
                ccs[cc]["names"].update(hw["gpu_names"])

    out = {
        "gpu_devices": {
            d: {"pci_ids_name": pci.get(d), "profiles": s["profiles"], "models": len(s["models"]),
                "example_models": sorted(s["models"])[:5], "cooccurring_names": dict(s["names"].most_common()),
                "cooccurring_cc": dict(s["cc"])}
            for d, s in sorted(dev.items())
        },
        "gpu_names": {
            n: {"profiles": s["profiles"], "models": len(s["models"]), "example_models": sorted(s["models"])[:5],
                "cooccurring_devices": {f"{d}:10de": c for d, c in s["devices"].most_common()},
                "cooccurring_device_names": {d: pci.get(d) for d in s["devices"]},
                "cooccurring_cc": dict(s["cc"])}
            for n, s in sorted(names.items(), key=lambda kv: kv[0].lower())
        },
        "compute_capabilities": {
            c: {"profiles": s["profiles"], "models": len(s["models"]), "cooccurring_names": dict(s["names"].most_common())}
            for c, s in sorted(ccs.items())
        },
    }
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
