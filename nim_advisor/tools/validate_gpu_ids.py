"""Validate every NIM gpu_device id and internal gpu name against primary PCI-id sources.

For each gpu_device id found in nims_catalog/*/model_manifest.yaml:
  * is it a real NVIDIA device?  (NVIDIA driver README Appendix A + pci.ids, see tools/gpu_sources.py)
  * which reference GPU is it?   (reference/gpu_reference.json)
  * do the internal 'gpu' names written next to it agree?
For each internal gpu name:
  * which GPU does it denote (official nvidia-smi / Kubernetes name, alias, device co-occurrence, chip family)?
  * which profiles pair it with a device id of a different GPU (manifest errors)?

Writes reference/gpu_device_validation.json (read by the importer and shown in the website)
and optionally a Markdown report.

Usage (from nim_explorer/nim_advisor):
  python tools/validate_gpu_ids.py [--catalog ../nims_catalog] [--reference ../reference] [--report ../reference/gpu_validation_report.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.normalize import iter_profiles, load_manifest, norm_name, normalize_profile  # noqa: E402

MAX_LOCATIONS = 4
_NAME_LINE = re.compile(r"^\s*(?:gpu|gpu_key):\s*(.+?)\s*$")


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _names_in(value: str) -> list[str]:
    return [v.strip().strip("'\"") for v in re.split(r"[,;]", value.strip().strip("'\""))]


def device_lines(text: str, d: str) -> list[int]:
    """1-based lines of 'gpu_device: <d>[:10de]' (also device_id, quotes / case ignored)."""
    pat = re.compile(rf"^\s*(?:gpu_device|device_id):\s*['\"]?(?:0x)?{d}(?::10de)?['\"]?\s*$", re.I | re.M)
    return [_line_no(text, m.start()) for m in pat.finditer(text)]


def name_lines(text: str, name: str) -> list[int]:
    """1-based lines of 'gpu: <name>' including comma-separated lists ('gpu: L4, L40, RTX4090')."""
    return [_line_no(text, m.start()) for m in re.finditer(r"^\s*(?:gpu|gpu_key):\s*(.+?)\s*$", text, re.M)
            if name in _names_in(m.group(1))]


def pair_lines(lines: list[str], text: str, d: str, name: str, window: int = 12) -> list[int]:
    """Device lines that have 'gpu: <name>' within the same tag block (+/- window lines)."""
    for ln in device_lines(text, d):
        for j in range(max(0, ln - 1 - window), min(len(lines), ln + window)):
            m = _NAME_LINE.match(lines[j])
            if m and name in _names_in(m.group(1)):
                return [ln]
    return []


def main() -> None:
    here = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(here / "nims_catalog"))
    ap.add_argument("--reference", default=str(here / "reference"))
    ap.add_argument("--report", default=None, help="optional Markdown report path")
    a = ap.parse_args()

    catalog, ref_dir = Path(a.catalog), Path(a.reference)
    gpus = json.loads((ref_dir / "gpu_reference.json").read_text(encoding="utf-8"))["gpus"]
    src = json.loads((ref_dir / "sources" / "nvidia_pci_ids.json").read_text(encoding="utf-8"))
    src_dev = src["devices"]
    readme = src["sources"]["nvidia_driver_readme"]
    pci = src["sources"]["pci_ids"]
    readme_ref = f"NVIDIA driver {readme['version']} README Appendix A ({readme['url']})"
    pci_ref = f"pci.ids {pci['version']} ({pci['url']})"

    by_id = {g["id"]: g for g in gpus}
    by_dev: dict[str, list[str]] = defaultdict(list)
    official: dict[str, set[str]] = defaultdict(set)   # norm name -> gpu ids (nvidia-smi / k8s names)
    alias: dict[str, set[str]] = defaultdict(set)      # norm name -> gpu ids (every alias, as the importer does)
    internal: dict[str, set[str]] = defaultdict(set)   # raw internal name -> gpu ids
    for g in gpus:
        for d in g["pci_device_ids"]:
            by_dev[d].append(g["id"])
        for n in [*g.get("nvidia_smi_names", []), *g.get("k8s_gpu_product", [])]:
            official[norm_name(n)].add(g["id"])
        for n in [g["id"], g["name"], *g.get("aliases", []), *g.get("k8s_gpu_product", []), *g.get("internal_names", [])]:
            alias[norm_name(n)].add(g["id"])
        for n in g.get("internal_names", []):
            internal[n].add(g["id"])

    # ------------------------------------------------------------ evidence
    dev = defaultdict(lambda: {"profiles": 0, "models": set(), "names": Counter(), "loc": []})
    names = defaultdict(lambda: {"profiles": 0, "models": set(), "devices": Counter(), "cc": Counter(), "loc": []})
    pairs = defaultdict(lambda: {"profiles": 0, "models": set(), "loc": []})
    for mf in sorted(catalog.glob("*/model_manifest.yaml")):
        text = mf.read_text(encoding="utf-8", errors="replace")
        try:
            profiles = [normalize_profile(p) for p in iter_profiles(load_manifest(text))]
        except Exception as exc:  # noqa: BLE001
            print(f"skip {mf.parent.name}: {exc}", file=sys.stderr)
            continue
        rel = f"nims_catalog/{mf.parent.name}/model_manifest.yaml"
        model = mf.parent.name
        for p in profiles:
            hw = p["hw"]
            for d in hw["gpu_device_ids"]:
                e = dev[d]
                e["profiles"] += 1
                e["models"].add(model)
                e["names"].update(hw["gpu_names"])
                for n in hw["gpu_names"]:
                    k = pairs[(d, n)]
                    k["profiles"] += 1
                    k["models"].add(model)
            for n in hw["gpu_names"]:
                e = names[n]
                e["profiles"] += 1
                e["models"].add(model)
                e["devices"].update(hw["gpu_device_ids"])
                if hw["compute_capability"]:
                    e["cc"][hw["compute_capability"]] += 1
        lines = text.splitlines()
        for d, e in dev.items():
            if model in e["models"] and len(e["loc"]) < MAX_LOCATIONS:
                e["loc"] += [f"{rel}:{ln}" for ln in device_lines(text, d)[:1]]
        for n, e in names.items():
            if model in e["models"] and len(e["loc"]) < MAX_LOCATIONS:
                e["loc"] += [f"{rel}:{ln}" for ln in name_lines(text, n)[:1]]
        for (d, n), e in pairs.items():
            if model in e["models"] and len(e["loc"]) < MAX_LOCATIONS:
                e["loc"] += [f"{rel}:{ln}" for ln in pair_lines(lines, text, d, n)[:1]]

    def gname(ids) -> str:
        return ", ".join(by_id[i]["name"] for i in ids if i in by_id) or "-"

    def name_gpus(n: str) -> list[str]:
        return sorted(alias.get(norm_name(n), set()) | internal.get(n, set()))

    # ------------------------------------------------------------ device verdicts
    devices, issues = {}, []
    for d, e in sorted(dev.items()):
        s = src_dev.get(d) or {}
        drv, pname = s.get("driver_names") or [], s.get("pci_ids_name")
        ref = by_dev.get(d, [])
        seen = ", ".join(f"{n} x{c}" for n, c in e["names"].most_common()) or "none"
        disagree = {n: c for n, c in e["names"].items() if name_gpus(n) and not set(name_gpus(n)) & set(ref)}
        if not drv and not pname:
            verdict = "refuted"
            summary = (f"{d}:10de is not an NVIDIA device id: absent from the {readme['version']} driver README and "
                       f"from pci.ids {pci['version']}. Internal names used with it: {seen}.")
        elif not drv:
            verdict = "refuted" if disagree or not ref else "uncertain"
            summary = (f"{d}:10de is only in pci.ids as '{pname}' (not a product in the driver README). "
                       f"Internal names used with it: {seen}.")
        elif not ref:
            verdict = "uncertain"
            summary = f"{d}:10de = '{', '.join(drv)}' but no reference GPU carries this id."
        else:
            verdict = "confirmed"
            summary = (f"{d}:10de = '{' / '.join(drv)}' (driver README) and '{pname}' (pci.ids) -> {gname(ref)}. "
                       f"Internal names used with it: {seen}.")
        if disagree:
            summary += " Mismatch: " + "; ".join(f"'{n}' x{c} denotes {gname(name_gpus(n))}" for n, c in disagree.items())
        devices[d] = {
            "verdict": verdict, "gpu_ids": ref, "summary": summary, "driver_names": drv, "pci_ids_name": pname,
            "profiles": e["profiles"], "models": len(e["models"]),
            "sources": [readme_ref, pci_ref, *e["loc"]],
        }

    # ------------------------------------------------------------ name verdicts
    gpu_names = {}
    for n, e in sorted(names.items(), key=lambda kv: kv[0].lower()):
        ids = name_gpus(n)
        devs = e["devices"]
        dev_gpus = {d: by_dev.get(d, []) for d in devs}
        agree = {d: c for d, c in devs.items() if set(dev_gpus[d]) & set(ids)}
        wrong = {d: c for d, c in devs.items() if dev_gpus[d] and not set(dev_gpus[d]) & set(ids)}
        unknown = {d: c for d, c in devs.items() if not dev_gpus[d]}
        cc = ", ".join(sorted(e["cc"]))
        if norm_name(n) in official and set(ids) & official[norm_name(n)]:
            basis = "official nvidia-smi / Kubernetes product name"
        elif n in internal and agree:
            basis = "gpu_device co-occurrence"
        elif ids and any(n.upper() == (by_id[i].get("chip") or "")[:len(n)].upper() for i in ids):
            basis = f"chip family {n} (compute capability {cc})" if cc else f"chip family {n}"
        elif ids and n in internal:
            basis = "manifest description"
        elif ids:
            basis = "alias"
        else:
            basis = None
        if not ids:
            verdict = "unresolved"
            summary = (f"'{n}' is not an NVIDIA product name in the driver README / pci.ids and has no gpu_device next to "
                       f"it" + (f"; its profiles are matched by compute capability {cc}." if cc else "."))
        else:
            verdict = "confirmed" if basis.startswith(("official", "gpu_device", "alias")) else "inferred"
            summary = f"'{n}' = {gname(ids)} ({basis})."
            if agree:
                summary += " Paired with " + ", ".join(f"{d}:10de x{c}" for d, c in agree.items()) + "."
            if not devs and cc:
                summary += f" No gpu_device: engines selected by compute capability {cc}."
        if wrong:
            summary += " Manifest error: " + "; ".join(f"{d}:10de x{c} is {gname(dev_gpus[d])}" for d, c in wrong.items()) + "."
        if unknown:
            summary += " Invalid device ids: " + ", ".join(f"{d}:10de x{c}" for d, c in unknown.items()) + "."
        gpu_names[n] = {"verdict": verdict, "gpu_ids": ids, "basis": basis, "summary": summary,
                        "profiles": e["profiles"], "models": len(e["models"]),
                        "compute_capabilities": sorted(e["cc"]), "sources": [readme_ref, pci_ref, *e["loc"]]}

    # ------------------------------------------------------------ manifest issues (one row per device/name pair)
    for (d, n), e in sorted(pairs.items()):
        ref, ids = by_dev.get(d, []), name_gpus(n)
        if not ref:
            kind = "unknown_device_id"
            text = (f"gpu '{n}' is written with gpu_device {d}:10de, which is "
                    + (f"'{src_dev[d]['pci_ids_name']}' in pci.ids" if (src_dev.get(d) or {}).get("pci_ids_name")
                       else "not an NVIDIA device id")
                    + (f"; {gname(ids)} is " + ", ".join(f"{x}:10de" for i in ids for x in by_id[i]["pci_device_ids"]) if ids else "")
                    + ". NIM cannot match these profiles on any GPU.")
        elif ids and not set(ref) & set(ids):
            kind = "device_name_mismatch"
            text = (f"gpu '{n}' ({gname(ids)}) is written with gpu_device {d}:10de, which is {gname(ref)}. "
                    f"NIM matches gpu_device, so these profiles are selected on {gname(ref)}, not on {gname(ids)}.")
        else:
            continue
        issues.append({"kind": kind, "gpu_device": f"{d}:10de", "gpu_name": n, "device_gpu_ids": ref, "name_gpu_ids": ids,
                       "profiles": e["profiles"], "models": sorted(e["models"]), "explanation": text,
                       "locations": e["loc"]})
    for n, v in gpu_names.items():
        if v["verdict"] == "unresolved":
            issues.append({"kind": "unresolved_name", "gpu_device": None, "gpu_name": n, "device_gpu_ids": [],
                           "name_gpu_ids": [], "profiles": v["profiles"], "models": sorted(names[n]["models"]),
                           "explanation": v["summary"], "locations": names[n]["loc"]})

    counts = {
        "gpu_devices": len(devices), "gpu_names": len(gpu_names),
        "device_verdicts": dict(Counter(v["verdict"] for v in devices.values())),
        "name_verdicts": dict(Counter(v["verdict"] for v in gpu_names.values())),
        "manifest_issues": dict(Counter(i["kind"] for i in issues)),
    }
    out = {"meta": {"generated": date.today().isoformat(), "sources": src["sources"], "counts": counts},
           "gpu_devices": devices, "gpu_names": gpu_names, "manifest_issues": issues}
    (ref_dir / "gpu_device_validation.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8", newline="\n")
    print(json.dumps(counts, indent=2))

    if a.report:
        write_report(Path(a.report), out, by_id, readme_ref, pci_ref)


def write_report(path: Path, out: dict, by_id: dict, readme_ref: str, pci_ref: str) -> None:
    def cell(s) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    c = out["meta"]["counts"]
    lines = [
        "# NIM gpu_device / internal GPU name validation",
        "",
        f"Generated {out['meta']['generated']} by `nim_advisor/tools/validate_gpu_ids.py` from `nims_catalog/*/model_manifest.yaml`.",
        f"Primary sources: {readme_ref}; {pci_ref}.",
        "",
        f"- gpu_device ids: {c['gpu_devices']} ({c['device_verdicts']})",
        f"- internal gpu names: {c['gpu_names']} ({c['name_verdicts']})",
        f"- manifest issues: {c['manifest_issues']}",
        "",
        "## Manifest issues",
        "",
        "| Kind | gpu_device | gpu name | Profiles | NIMs | Explanation |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for i in out["manifest_issues"]:
        lines.append(f"| {i['kind']} | {cell(i['gpu_device'] or '-')} | {cell(i['gpu_name'])} | {i['profiles']} | "
                     f"{cell(', '.join(i['models'][:6]) + (' ...' if len(i['models']) > 6 else ''))} | {cell(i['explanation'])} |")
    lines += ["", "## gpu_device ids", "", "| gpu_device | Verdict | GPU | Driver README name | pci.ids name | Profiles | NIMs |",
              "| --- | --- | --- | --- | --- | ---: | ---: |"]
    for d, v in out["gpu_devices"].items():
        lines.append(f"| `{d}:10de` | {v['verdict']} | {cell(', '.join(by_id[i]['name'] for i in v['gpu_ids']) or '-')} | "
                     f"{cell(' / '.join(v['driver_names']) or '-')} | {cell(v['pci_ids_name'] or '-')} | {v['profiles']} | {v['models']} |")
    lines += ["", "## Internal gpu names", "", "| Name | Verdict | GPU | Basis | Profiles | Evidence |",
              "| --- | --- | --- | --- | ---: | --- |"]
    for n, v in out["gpu_names"].items():
        lines.append(f"| `{cell(n)}` | {v['verdict']} | {cell(', '.join(by_id[i]['name'] for i in v['gpu_ids']) or '-')} | "
                     f"{cell(v['basis'] or '-')} | {v['profiles']} | {cell(v['summary'])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"report: {path}")


if __name__ == "__main__":
    main()
