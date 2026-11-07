# NIM Profile Advisor for HPE Private Cloud AI

[![CI](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/ci.yml/badge.svg)](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/ci.yml)
[![Container image](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/image.yml/badge.svg)](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/image.yml)
[![PCAI Helm chart](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/pcai-chart.yml/badge.svg)](https://github.com/osadsanu/nim-profile-advisor/actions/workflows/pcai-chart.yml)

Downloads the NIM profile manifests of every model in `nim_catalog.json`, validates the GPU identifiers they
use, stores everything in MongoDB and serves a website that recommends the best NIM profile for a GPU model and
GPU count (with HPE Private Cloud AI system presets). Use it to pick the `NIM_MODEL_PROFILE` to deploy, or to
pre-download for an air-gapped PCAI system, before pulling a multi-GB NIM image. New NIMs can be loaded from the
website (one image or a whole catalog JSON), and the app deploys to PCAI as an imported framework (Helm chart).

## Deploy

Requirements: Docker with Compose v2. Every option serves the website on http://localhost:8080.

**A. Published image (no build)** - built and pushed to GHCR by the *Container image* workflow:

```bash
git clone https://github.com/osadsanu/nim-profile-advisor.git && cd nim-profile-advisor
cp .env.example .env     # set MONGO_ROOT_PASSWORD, NGC_API_KEY and NIM_ADVISOR_IMAGE=ghcr.io/osadsanu/nim-profile-advisor:latest
docker compose pull
docker compose up -d     # MongoDB + import of the repository data + website
```

**B. Build from source** (build context: the repository root, `nim_advisor/Dockerfile`):

```bash
cp .env.example .env     # set MONGO_ROOT_PASSWORD (and NGC_API_KEY for the Load NIMs tab)
docker compose up -d --build
```

**C. Load the exported database instead of importing** (see [Database export](#database-export)):

```bash
docker compose up -d mongo
docker compose run --rm db-restore
docker compose up -d --no-deps web
```

**D. HPE Private Cloud AI**: import the Helm chart as a framework, see
[helm/nim-profile-advisor/README.md](helm/nim-profile-advisor/README.md).

Check a deployment with `python nim_advisor/tools/smoke_test.py http://localhost:8080` (standard library only).

Ports are bound to `127.0.0.1` and the website has no authentication. To serve it to other users, change the `web`
port mapping in `docker-compose.yml` and put it behind a reverse proxy with TLS and authentication (on PCAI, set
`pcai.sso: true` in the chart), or set `IMPORT_ENABLED=false` for a read-only site. Optional DB
browser: `docker compose --profile admin up -d mongo-express` (http://localhost:8081). Corporate root CAs needed
for the image build (e.g. Zscaler) go in `nim_advisor/certs/`.

## Load NIMs from the website

The **Load NIMs** tab adds NIM images that are not in the database yet, or refreshes existing ones:

- **One image**: an image reference (`nvcr.io/nim/meta/llama-3.1-8b-instruct:1.8.4`), a `docker pull ...` command or
  an NGC catalog URL (`https://catalog.ngc.nvidia.com/orgs/nim/teams/meta/containers/llama-3.1-8b-instruct?version=1.8.4`).
- **Bulk import**: a JSON file in the `nim_catalog.json` format (each entry needs an `image`; name, displayName,
  description and labels are kept) or a plain array of images. A preview shows which entries are new, already have
  profiles (skipped unless *Re-fetch* is ticked), are duplicates or are invalid.

The website runs `nim_profile_finder_v2.sh` for every image in a background job (`FETCH_WORKERS` in parallel, NGC
login from `NGC_API_KEY`), parses the manifest like the importer and updates MongoDB right away: model, profiles and
GPU validation. Jobs show progress, the finder log of every image (NGC key redacted) and can be cancelled or retried.
A failed refresh never removes profiles that are already stored. The loaded NIMs are kept in the
`catalog_additions` collection and merged into every full import, so `docker compose run --rm importer` does not
drop them.

| Setting (`.env` / chart `env`) | Default | |
| --- | --- | --- |
| `NGC_API_KEY` | empty | NGC API key for nvcr.io (only the web container gets it) |
| `FETCH_WORKERS` | `4` | images fetched in parallel |
| `FETCH_TIMEOUT` / `FETCH_MAX_FILE_MB` | `900` / `1024` | per-image timeout (seconds) and size cap of a downloaded layer |
| `IMPORT_ENABLED` | `true` | `false` = read-only website |

API: `POST /api/import/image`, `POST /api/import/preview`, `POST /api/import/bulk`, `GET /api/import/jobs[/<id>]`,
`POST /api/import/jobs/<id>/cancel|retry`, `GET /api/import/config`.

## Database export

`db_export/` is a snapshot of the MongoDB database `nim_advisor` (19 MB):

| Path | Content |
| --- | --- |
| `db_export/mongodump/nim_advisor/` | `mongodump --gzip` of every collection, incl. indexes and `$jsonSchema` validators |
| `db_export/json/<collection>.json` | `mongoexport` output: one document per line, MongoDB relaxed Extended JSON, sorted by `_id`. Files over 10 MB are gzipped (`models.json.gz`, `profiles.json.gz`) |
| `db_export/export_info.json` | Export time, MongoDB version, source import run, document counts, file sizes |

```bash
docker compose run --rm db-export                           # re-create db_export/ from the running database
docker compose run --rm db-restore                          # restore it (replaces the collections)
docker compose run --rm -e RESTORE_DB=other_db db-restore   # restore into another database
```

Both commands run inside the `mongo:8.0` image, so no local MongoDB tools are needed. Without Docker:
`mongorestore --gzip --drop --dir db_export/mongodump --uri <mongodb-uri>`.

## CI and published image

| Workflow | Runs | |
| --- | --- | --- |
| [`ci.yml`](.github/workflows/ci.yml) | push, pull request | shell syntax, unit tests (`nim_advisor/tests`), import of `nim_catalog.json` + `nims_catalog/` + `reference/` into MongoDB, API smoke test (`nim_advisor/tools/smoke_test.py`) |
| [`image.yml`](.github/workflows/image.yml) | push, pull request, `v*` tags | builds the image, runs the unit tests and the finder tools inside it, starts it against MongoDB (start-up import of the bundled catalog) and smoke-tests it; pushes and tags publish `ghcr.io/osadsanu/nim-profile-advisor` with `latest`, `sha-<commit>` and, for a tag `v1.2.3`, `1.2.3` and `1.2` |
| [`pcai-chart.yml`](.github/workflows/pcai-chart.yml) | push, pull request | lints the Helm chart, validates the manifests (Kubernetes, Istio, Kyverno schemas), installs it in kind with the new image, smoke-tests it and uploads the packaged chart + logo (artifact `pcai-framework`) |

The workflows run MongoDB 7.0: MongoDB 8 refuses to start on the 7.0 kernel of the GitHub runners
(SERVER-121912). docker compose and the chart use 8.0.

## Layout

| Path | Content |
| --- | --- |
| `nim_catalog.json` | PCAI NIM catalog (304 images) |
| `nim_profile_finder_v2.sh` | Gets `model_manifest.yaml` + `nim_profiles.txt` of one image with skopeo (no full pull) |
| `bulk_fetch_profiles.sh` | Runs the finder for every catalog entry in parallel |
| `nims_catalog/<repo>_<tag>/` | One folder per image (`:` replaced by `_`; private-mirror entries end in `_ezmeral-common`). `_fetch_status.tsv` holds the fetch results |
| `reference/gpu_reference.json` | GPU catalog: PCI device ids, nvidia-smi / Kubernetes names, NIM internal names, VRAM, compute capability |
| `reference/pcai_configurations.json` | PCAI G2 (current) and G1 (legacy) systems: GPUs per worker node, nodes, MIG partitions, NVLink |
| `reference/nim_selection_rules.json` | How NIM selects a profile (NVIDIA sources) |
| `reference/gpu_device_validation.json` | Generated by `validate_gpu_ids.py`: verdict for every `gpu_device` id and internal `gpu` name, plus manifest issues |
| `reference/gpu_validation_report.md` | Same validation as a readable report |
| `reference/sources/nvidia_pci_ids.json` | NVIDIA subset of the driver README (Appendix A) and pci.ids, used as primary validation source |
| `nim_advisor/` | FastAPI app + static website (`app/`; `fetcher.py` runs the Load NIMs jobs), tools (`tools/`), unit tests (`tests/`), `Dockerfile` (build context: repository root; bundles the finder script and the catalog data) |
| `helm/nim-profile-advisor/` | HPE PCAI framework Helm chart (`helm/nim-profile-advisor.png`: icon for the import wizard) |
| `scripts/` | `db_export.sh` / `db_restore.sh` used by the `db-export` / `db-restore` compose services |
| `db_export/` | Database snapshot (see above) |
| `docker-compose.yml` | `mongo`, `importer`, `web`; `db-export` / `db-restore` (profile `tools`); `mongo-express` (profile `admin`) |

## Refresh the data

Single images or a new catalog JSON: use the [Load NIMs](#load-nims-from-the-website) tab. To refresh the whole
repository copy:

```bash
# 1. manifests (inside WSL / Linux with skopeo, jq and an NGC login in ~/.config/containers/auth.json)
JOBS=6 ./bulk_fetch_profiles.sh            # FORCE=1 re-fetches, ONLY='<regex>' limits the images

# 2. validate gpu_device ids and internal gpu names (host Python with PyYAML)
python nim_advisor/tools/validate_gpu_ids.py --report reference/gpu_validation_report.md

# 3. re-import into MongoDB (the website reloads automatically), then refresh the snapshot
docker compose run --rm importer
docker compose run --rm db-export
```

Catalog entries that mirror nvcr.io in a private registry use the placeholder prefix
`registry.example.com/ezmeral-common/`; their manifests are fetched from nvcr.io. If your catalog uses a real
mirror, set `INTERNAL_REGISTRY_PREFIX` in `.env` and in the shell that runs `bulk_fetch_profiles.sh`.

To refresh the PCI-id sources, download the driver README of the version in
`https://download.nvidia.com/XFree86/Linux-x86_64/latest.txt` (`.../<version>/README/supportedchips.html`) and
`https://pci-ids.ucw.cz/v2.2/pci.ids`, then run:

```bash
python nim_advisor/tools/gpu_sources.py --readme supportedchips.html --readme-version <version> \
    --pci-ids pci.ids --out reference/sources/nvidia_pci_ids.json
```

Unit tests:

```bash
docker compose run --rm -v ./nim_advisor/tests:/srv/tests:ro --entrypoint python importer -m unittest discover -s tests -v
```

## Database

MongoDB 8, database `nim_advisor`. Every collection has a `$jsonSchema` validator (see `nim_advisor/app/schema.py`):
`models` (one per catalog entry), `profiles` (normalized profile + raw tags + workspace files), `gpus`,
`pcai_configurations`, `gpu_device_validation`, `manifest_issues` and `import_runs`; the Load NIMs tab adds
`catalog_additions` (loaded images and their manifests), `import_jobs`, `import_logs` and `locks`. The import
replaces the catalog collections (website additions are merged back in), so it can be re-run at any time;
`IMPORT_MODE=if-changed` skips it when the data and the import code did not change.

## Status (2026-11-05)

- 246 of 304 catalog images have a manifest (3,276 profiles). The other 58: 34 are denied by NGC entitlement
  (production-branch / STIG images), 23 images contain no `model_manifest.yaml` (operators, sample clients, ...),
  1 tag does not exist (`vllm-base-enterprise-nightly`).
- 39 `gpu_device` ids: 36 confirmed by the NVIDIA driver README and pci.ids. `2e01`, `2e02` and `2e14`
  (audio2face-3d: RTX5080 / RTX5090 / rtx6000-blackwell-sv) are not those GPUs.
- 115 internal `gpu` names: 110 confirmed, 4 inferred (chip families `GA102`, `GA104`, `GB20`; `NVIDIA-GB300-WS` = DGX
  Station), 1 unknown (`B40`).
- Manifest errors: 45 profiles in 17 older LLM NIMs tag `gpu: L40S` with `26b5:10de` (the L40 id), so NIM does not
  auto-select them on an L40S (`26b9`); `qwen3-32b` tags the H20-3e id `232c` as `H20`.
- PCAI GPUs: G2 uses the RTX PRO 6000 Blackwell Server Edition (`2bb5:10de`) and the H200 **NVL** (`233b:10de`, not
  the SXM `2335:10de` that many optimized profiles target); G1 uses the L40S (`26b9:10de`) and H100 NVL (`2321:10de`).
  Confirm on a node with `lspci -nn | grep -i nvidia`.

## Data sources and trademarks

- `nims_catalog/` holds the profile manifests NVIDIA ships in the NIM container images on NGC. Using NIM is
  subject to NVIDIA's license terms. NVIDIA, NIM and NGC are trademarks of NVIDIA Corporation.
- `reference/sources/nvidia_pci_ids.json` is derived from the NVIDIA Linux driver README (Appendix A) and
  [The PCI ID Repository](https://pci-ids.ucw.cz/) (GNU GPL v2+ or 3-clause BSD).
- PCAI configurations come from the public HPE Private Cloud AI QuickSpecs (a50009216enw). Citations of the form
  `Guides/PCAI_docs/PCAI_2026.07.1/<chapter>.md:<lines>` refer to a Markdown conversion of the *HPE AI Essentials
  Software 2026.07.1 Documentation* (September 2026), which is not included here.
- Independent project; not an official HPE or NVIDIA product.
