"""Build reference/sources/nvidia_pci_ids.json from the two primary PCI-id sources.

Sources (download them first, TLS verification stays on):
  * NVIDIA Linux driver README, Appendix A "Supported NVIDIA GPU Products"
      https://download.nvidia.com/XFree86/Linux-x86_64/<version>/README/supportedchips.html
      (<version> = first field of https://download.nvidia.com/XFree86/Linux-x86_64/latest.txt)
  * The PCI ID Repository  https://pci-ids.ucw.cz/v2.2/pci.ids

Only vendor 10de (NVIDIA) is kept.

Usage:
  python tools/gpu_sources.py --readme supportedchips.html --readme-version 595.80 \
      --pci-ids pci.ids --out ../reference/sources/nvidia_pci_ids.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path

ROW = re.compile(r'<tr id="devid[^"]*">\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>', re.S | re.I)
TAG = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return " ".join(html.unescape(TAG.sub("", cell)).split())


def parse_readme(path: Path) -> dict[str, dict]:
    """{device_id: {"names": [...], "subsystems": {"10de:18be": "NVIDIA H200"}, "legacy": False}}"""
    devices: dict[str, dict] = {}
    for name, ids, flags in ROW.findall(path.read_text(encoding="utf-8", errors="replace")):
        name, parts = _text(name), _text(ids).lower().split()
        if not parts or not re.fullmatch(r"[0-9a-f]{4}", parts[0]):
            continue
        d = devices.setdefault(parts[0], {"names": [], "subsystems": {}, "flags": []})
        if name not in d["names"]:
            d["names"].append(name)
        if len(parts) == 3:
            d["subsystems"][f"{parts[1]}:{parts[2]}"] = name
        flag = _text(flags)
        if flag and flag not in d["flags"]:
            d["flags"].append(flag)
    return devices


def parse_pci_ids(path: Path) -> tuple[str | None, dict[str, dict]]:
    """Return (version, {device_id: {"name": ..., "subsystems": {"10de:1996": "H200 NVL"}}}) for vendor 10de."""
    version, devices, in_nvidia, current = None, {}, False, None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            m = re.match(r"#\s*Version:\s*(\S+)", line)
            if m:
                version = m.group(1)
            continue
        if not line.strip():
            continue
        if not line.startswith("\t"):
            in_nvidia = line.lower().startswith("10de ")
            current = None
            continue
        if not in_nvidia:
            continue
        if line.startswith("\t\t"):
            m = re.match(r"\t\t([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+(.*)", line)
            if m and current:
                devices[current]["subsystems"][f"{m.group(1).lower()}:{m.group(2).lower()}"] = m.group(3).strip()
            continue
        m = re.match(r"\t([0-9a-fA-F]{4})\s+(.*)", line)
        if m:
            current = m.group(1).lower()
            devices[current] = {"name": m.group(2).strip(), "subsystems": {}}
    return version, devices


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", required=True)
    ap.add_argument("--readme-version", required=True)
    ap.add_argument("--pci-ids", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    readme = parse_readme(Path(a.readme))
    pci_version, pci = parse_pci_ids(Path(a.pci_ids))
    devices = {}
    for dev in sorted(set(readme) | set(pci)):
        r, p = readme.get(dev), pci.get(dev)
        devices[dev] = {
            "driver_names": r["names"] if r else [],
            "driver_subsystems": r["subsystems"] if r else {},
            "pci_ids_name": p["name"] if p else None,
            "pci_ids_subsystems": p["subsystems"] if p else {},
        }
    out = {
        "generated": date.today().isoformat(),
        "sources": {
            "nvidia_driver_readme": {
                "version": a.readme_version,
                "url": f"https://download.nvidia.com/XFree86/Linux-x86_64/{a.readme_version}/README/supportedchips.html",
                "devices": len(readme),
            },
            "pci_ids": {"version": pci_version, "url": "https://pci-ids.ucw.cz/v2.2/pci.ids", "devices": len(pci)},
        },
        "devices": devices,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"driver README devices={len(readme)}  pci.ids {pci_version} devices={len(pci)}  merged={len(devices)}")


if __name__ == "__main__":
    main()
