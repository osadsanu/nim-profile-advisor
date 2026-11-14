# Get the best performance from every NIM on HPE Private Cloud AI in 3 steps

> **Choose the right profile before you deploy, not after.**
> NIM Profile Advisor tells you which NVIDIA NIM profile fits your PCAI GPUs before you download a multi-GB image.

---

## Step 1 - The problem: NIM may not choose the best profile for your system

Each NVIDIA NIM ships with many **profiles**. A profile is a prebuilt engine for one combination of GPU model,
GPU count, precision (FP8, BF16, ...), backend (TensorRT-LLM, vLLM, ...) and goal (latency or throughput).
Some NIMs have dozens of them.

When the container starts, NIM selects a profile automatically. On PCAI it often picks a weaker one:

- **The GPU ID does not match.** Many optimized profiles target the H200 **SXM** (`2335`), but PCAI G2 uses the
  H200 **NVL** (`233b`). NIM ignores the optimized engine and falls back to a generic one.
- **Some manifests contain errors.** 45 profiles in 17 older LLM NIMs label the L40 ID as `L40S`, so NIM never
  selects them on a PCAI G1 L40S node.
- **Trial and error costs time.** Without the advisor, you have to pull the image, start it, read the logs,
  change settings and try again.

**Result:** lower throughput, higher latency and idle GPU capacity that you are still paying for.

---

## Step 2 - Find the best profile for your system

Open **NIM Profile Advisor**, go to the **Recommend** tab and describe your hardware:

| Field | Example |
| --- | --- |
| Start from a PCAI system | *PCAI Medium (G2) - H200 NVL* (fills in GPU model, GPUs per node and nodes) |
| GPU model | `H200 NVL`, `L40S`, `233b` ... |
| GPUs per node / Nodes | `4` / `1` |
| Optimize for | Maximum throughput · Lowest latency · Highest precision · Fewest GPUs |
| Model search | `llama`, `qwen`, `embed` ... |

Click **Recommend profiles**. Within seconds you see:

- the profiles that are **compatible** with your system, ranked by your goal
- **exact GPU matches** compared with generic fallbacks
- **why** a profile is blocked or flagged (not enough VRAM, wrong GPU ID, GPU count, ...)
- the **profile ID** to copy

The advisor uses data from **3,000+ profiles across 240+ NIMs**, checked against NVIDIA PCI IDs and the HPE PCAI
hardware configurations. If a NIM is missing, add it from the **Load NIMs** tab with an image reference or NGC URL.

---

## Step 3 - Deploy the NIM with the selected profile

Set one environment variable, `NIM_MODEL_PROFILE`, to the profile ID you copied. The advisor generates the snippet
for you.

**Kubernetes / PCAI (Helm values or manifest):**

```yaml
env:
  - name: NIM_MODEL_PROFILE
    value: "<profile-id-from-the-advisor>"
resources:
  limits:
    nvidia.com/gpu: 4
```

**Docker:**

```bash
docker run --rm --gpus 4 -e NGC_API_KEY \
  -e NIM_MODEL_PROFILE=<profile-id-from-the-advisor> \
  -p 8000:8000 nvcr.io/nim/<org>/<model>:<tag>
```

The NIM starts with the profile you selected. For an **air-gapped** PCAI system, use the same profile ID to
download only that engine in advance.

---

## Why use it

| Without the advisor | With NIM Profile Advisor |
| --- | --- |
| NIM selects a profile, and it may not be the best one | You choose the profile for your PCAI GPUs |
| Pull, run and check the logs, then repeat | Get a ranked list before you pull the image |
| Optimized engines are missed because of GPU ID mismatches | Mismatches are detected and explained |
| GPU capacity is wasted | Get more performance from the same hardware |

**Get started:** `docker compose up -d` and open http://localhost:8080, or import the Helm chart into PCAI as a
framework (see [helm/nim-profile-advisor/README.md](helm/nim-profile-advisor/README.md)).