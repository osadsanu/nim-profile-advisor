"use strict";

/* ---------- helpers ---------- */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtGB = (v) => (v == null ? "—" : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} GB`);
const fmtBytes = (b) => (b ? `${(b / 1e9).toFixed(1)} GB` : "—");
const short = (id) => (id && id.length > 16 ? `${id.slice(0, 12)}…` : id || "");
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(path, options = {}) {
  const init = { headers: { Accept: "application/json" } };
  if (options.body !== undefined) {
    init.method = options.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  } else if (options.method) {
    init.method = options.method;
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!r.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ") : data.detail;
    const err = new Error(data.error || (typeof detail === "string" && detail ? detail : r.statusText));
    err.data = data;
    err.status = r.status;
    throw err;
  }
  return data;
}
const apiPost = (path, body) => api(path, { body: body ?? {} });
const fmtTime = (s) => (s ? new Date(s).toLocaleString() : "—");

const STATUS_BADGE = { validated: "ok", mapped: "info", conflict: "warn", unknown: "bad" };
const FETCH_BADGE = { ok: "ok", skipped: "ok", failed: "bad", missing: "warn" };
const MATCH_BADGE = {
  gpu_device: ["ok", "Exact GPU match"],
  gpu_name: ["ok", "GPU-specific"],
  compute_capability: ["info", "Compute-capability engine"],
  generic: ["info", "Generic profile"],
};

const state = { gpus: [], pcai: [], stats: null, rec: null, filter: "all", models: [], validation: [] };

/* ---------- copy buttons ---------- */
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  try {
    await navigator.clipboard.writeText(b.dataset.copy);
    const old = b.textContent; b.textContent = "copied"; setTimeout(() => (b.textContent = old), 1200);
  } catch { /* clipboard may be unavailable */ }
});
const copyBtn = (text, label = "copy") => `<button type="button" class="small copy" data-copy="${esc(text)}">${label}</button>`;

/* ---------- tabs / routing ---------- */
function showTab(name) {
  $$(".panel").forEach((p) => (p.hidden = p.dataset.panel !== name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  if (name === "models" && !state.models.length) loadModels();
  if (name === "load") loadImportTab();
  if (name === "validation" && !state.validation.length) loadValidation();
  if (name === "pcai") renderPcai();
  if (name === "about") renderAbout();
}
function route() {
  const [name, query] = (location.hash.slice(1) || "recommend").split("?");
  showTab(["recommend", "models", "load", "validation", "pcai", "about"].includes(name) ? name : "recommend");
  if (name === "recommend" && query) {
    const p = new URLSearchParams(query);
    const f = $("#rec-form");
    for (const [k, v] of p) {
      const el = f.elements[k];
      if (!el) continue;
      if (el.type === "checkbox") el.checked = v === "true"; else el.value = v;
    }
    refreshMigOptions();
    resolveGpuPreview();
    runRecommend(false);
  }
}
window.addEventListener("hashchange", route);

/* ---------- init ---------- */
async function init() {
  try {
    const [stats, gpus, pcai] = await Promise.all([api("/api/stats"), api("/api/gpus"), api("/api/pcai")]);
    state.stats = stats; state.gpus = gpus; state.pcai = pcai;
  } catch (e) {
    $("#rec-results").innerHTML = `<div class="card error">Cannot reach the API: ${esc(e.message)}</div>`;
    return;
  }
  renderStats();
  const dl = $("#gpu-list");
  dl.innerHTML = state.gpus.map((g) => `<option value="${esc(g.name)}">${esc(g.in_pcai ? "PCAI · " : "")}${esc(g.pci_device_ids.join(", "))} · ${fmtGB(g.vram_gb)}</option>`).join("");
  $("#f-pcai").innerHTML += state.pcai.map((c) =>
    `<option value="${esc(c._id)}">${esc(c.name)} — ${esc(c.gpus_per_worker_node)}× ${esc(c.gpu ? c.gpu.name : c.gpu_model)} per node</option>`).join("");
  $("#f-goal").innerHTML = Object.entries(stats().goals || {}).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  const cats = Object.keys(stats().categories || {});
  for (const sel of ["#f-category", "#m-category"]) $(sel).innerHTML += cats.map((c) => `<option>${esc(c)}</option>`).join("");
  route();
  refreshJobs();
  watchSeed();
}
const stats = () => state.stats || {};

function renderStats() {
  const s = stats();
  const run = s.import_run || {};
  const when = run.finished_at ? new Date(run.finished_at).toLocaleString() : "never";
  const seeding = s.seed && ["pending", "running"].includes(s.seed.status);
  $("#stats").innerHTML = [
    ["models", s.models], ["profiles", s.profiles], ["GPUs", s.gpus], ["PCAI configs", s.pcai_configurations],
  ].map(([l, v]) => `<span class="stat"><b>${esc(v ?? 0)}</b> ${esc(l)}</span>`).join("") +
    `<span class="stat" title="last database update">updated ${esc(when)}</span>` +
    (seeding ? `<span class="stat busy"><span class="spinner small"></span> importing the bundled catalog…</span>` : "") +
    (s.seed && s.seed.status === "failed" ? `<span class="stat error" title="${esc(s.seed.message || "")}">bundled catalog import failed</span>` : "");
}

async function refreshStats() {
  try { state.stats = await api("/api/stats"); renderStats(); } catch { /* keep the old numbers */ }
}

// First start on Kubernetes: the website imports the catalog bundled in the image in the background
function watchSeed() {
  const s = stats();
  if (!s.seed || !["pending", "running"].includes(s.seed.status)) return;
  const timer = setInterval(async () => {
    await refreshStats();
    const st = (stats().seed || {}).status;
    if (["pending", "running"].includes(st)) return;
    clearInterval(timer);
    if (st === "done") location.reload();
  }, 5000);
}

/* ---------- recommend form ---------- */
const form = $("#rec-form");
$$(".quick button").forEach((b) => b.addEventListener("click", () => { $("#" + b.parentElement.dataset.target).value = b.dataset.v; }));

$("#f-pcai").addEventListener("change", (e) => {
  const c = state.pcai.find((x) => x._id === e.target.value);
  if (!c) return;
  $("#f-gpu").value = c.gpu ? c.gpu.name : c.gpu_model;
  $("#f-count").value = c.gpus_per_worker_node || 1;
  $("#f-nodes").value = 1;
  $("#f-vram").value = "";
  refreshMigOptions(c);
  resolveGpuPreview();
});

function refreshMigOptions(config) {
  const sel = $("#f-mig");
  const current = sel.value;
  const gpuText = $("#f-gpu").value.trim().toLowerCase();
  const configs = config ? [config] : state.pcai.filter((c) => c.gpu && [c.gpu.name, c.gpu.id, ...(c.gpu.aliases || [])]
    .some((a) => String(a).toLowerCase() === gpuText));
  const migs = [...new Set(configs.flatMap((c) => c.mig_profiles || []))];
  sel.innerHTML = `<option value="">Full GPU (no MIG)</option>` + migs.map((m) => `<option>${esc(m)}</option>`).join("");
  if (migs.includes(current)) sel.value = current;
}

const resolveGpuPreview = debounce(async () => {
  const q = $("#f-gpu").value.trim();
  const box = $("#gpu-resolve");
  if (!q) { box.innerHTML = ""; return; }
  try {
    const hits = await api(`/api/gpus/resolve?q=${encodeURIComponent(q)}`);
    if (!hits.length) { box.innerHTML = `<span class="error">No GPU matches “${esc(q)}”.</span>`; return; }
    if (hits.length > 1) {
      box.innerHTML = `<span class="muted">Several GPUs match — pick one:</span><br>` +
        hits.map((g) => `<button type="button" class="small cand" data-gpu="${esc(g.name)}">${esc(g.name)} <span class="muted">${esc(g.nim_gpu_device.join(", "))}</span></button>`).join("");
      return;
    }
    const g = hits[0];
    const v = g.validation.map((x) => `<code>${esc(x.gpu_device)}</code> <span class="badge ${STATUS_BADGE[x.status] || ""}">${esc(x.status)}</span>`).join(" ");
    box.innerHTML = `→ <b>${esc(g.name)}</b> · ${fmtGB(g.vram_gb)} · CC ${esc(g.compute_capability)}<br>${v || '<span class="muted">no PCI id</span>'}`;
    refreshMigOptions();
  } catch (e) { box.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
});
$("#f-gpu").addEventListener("input", resolveGpuPreview);
$("#gpu-resolve").addEventListener("click", (e) => {
  const b = e.target.closest("[data-gpu]");
  if (b) { $("#f-gpu").value = b.dataset.gpu; resolveGpuPreview(); }
});

form.addEventListener("submit", (e) => { e.preventDefault(); runRecommend(true); });

function formParams() {
  const p = new URLSearchParams();
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") { if (el.checked) p.set(el.name, "true"); }
    else if (el.value !== "") p.set(el.name, el.value);
  }
  return p;
}

async function runRecommend(pushHash) {
  const p = formParams();
  if (!p.get("gpu")) return;
  if (pushHash) history.replaceState(null, "", `#recommend?${p}`);
  $("#rec-results").innerHTML = `<div class="card empty"><span class="spinner"></span> Evaluating every profile…</div>`;
  $("#rec-gpu").innerHTML = ""; $("#rec-summary").innerHTML = ""; $("#rec-filters").hidden = true;
  try {
    state.rec = await api(`/api/recommend?${p}`);
    renderRecommendation();
  } catch (e) {
    const c = (e.data && e.data.gpu_candidates) || [];
    $("#rec-results").innerHTML = `<div class="card"><p class="error">${esc(e.message)}</p>` +
      c.map((g) => `<button type="button" class="small cand" data-gpu="${esc(g.name)}">${esc(g.name)}</button> `).join("") + "</div>";
    $$("#rec-results [data-gpu]").forEach((b) => b.addEventListener("click", () => { $("#f-gpu").value = b.dataset.gpu; runRecommend(true); }));
  }
}

function renderGpuCard(g, req) {
  const configs = g.pcai_configurations.map((c) => `<span class="badge accent">${esc(c.name)}</span>`).join(" ");
  const val = g.validation.map((v) =>
    `<div><code>${esc(v.gpu_device)}</code> <span class="badge ${STATUS_BADGE[v.status] || ""}">${esc(v.status)}</span>
      <span class="muted">${esc(v.profiles)} profiles / ${esc(v.models)} NIMs${v.internal_names.length ? " · internal names: " + esc(v.internal_names.join(", ")) : ""}</span></div>`).join("");
  return `<div class="card gpu-card">
    <div>
      <h2>${esc(g.name)} ${g.in_pcai ? '<span class="badge accent">used in PCAI</span>' : ""}</h2>
      <div class="kv">
        <div><span>Architecture</span><b>${esc(g.architecture)}</b></div>
        <div><span>Compute capability</span><b>${esc(g.compute_capability)}</b></div>
        <div><span>Memory per GPU</span><b>${fmtGB(req.vram_gb_per_device)}${req.mig ? ` (MIG ${esc(req.mig)})` : ""}</b></div>
        <div><span>Form factor</span><b>${esc(g.form_factor || "—")}</b></div>
        <div><span>FP8 / NVFP4</span><b>${g.fp8 ? "yes" : "no"} / ${g.nvfp4 ? "yes" : "no"}</b></div>
        <div><span>nvidia.com/gpu.product</span><b class="mono">${esc((g.k8s_gpu_product || []).join(", ") || "—")}</b></div>
        <div><span>Request</span><b>${esc(req.count)} GPU × ${esc(req.nodes)} node · ${esc(req.goal_label)}</b></div>
      </div>
      <div style="margin-top:10px"><span class="muted">NIM gpu_device ↔ internal model validation</span>${val || '<div class="muted">This GPU has no PCI id in the reference.</div>'}</div>
      ${configs ? `<div style="margin-top:8px">${configs}</div>` : ""}
    </div>
  </div>`;
}

function renderRecommendation() {
  const r = state.rec;
  $("#rec-gpu").innerHTML = renderGpuCard(r.gpu, r.request);
  const s = r.summary;
  const blockers = (s.top_blockers || []).map(([b, n]) => `<li>${esc(b)} <span class="muted">(${esc(n)} NIMs)</span></li>`).join("");
  $("#rec-summary").innerHTML = `<div class="summary">
      <div class="metric"><div class="v">${esc(s.compatible)}</div><div class="l">of ${esc(s.models)} NIMs can run</div></div>
      <div class="metric"><div class="v">${esc(s.exact_gpu_matches)}</div><div class="l">with an exact ${esc(r.gpu.name)} profile</div></div>
      <div class="metric"><div class="v">${esc(s.incompatible)}</div><div class="l">not compatible</div></div>
      <div class="metric"><div class="v">${esc(s.no_profiles)}</div><div class="l">without downloaded profiles</div></div>
    </div>${blockers ? `<div class="card" style="margin-top:10px"><b>Most common blockers</b><ul class="blockers">${blockers}</ul></div>` : ""}`;
  $("#rec-filters").hidden = false;
  renderResults();
}

$("#rec-filters").addEventListener("click", (e) => {
  const b = e.target.closest("[data-f]");
  if (!b) return;
  state.filter = b.dataset.f;
  $$("#rec-filters .chip").forEach((c) => c.classList.toggle("active", c === b));
  renderResults();
});

function profileLine(p) {
  return `<span class="pill">${esc(p.precision || "precision ?")}</span>
    <span class="pill">${esc(p.engine || "engine ?")}</span>
    <span class="pill">tp ${esc(p.tp)} × pp ${esc(p.pp)} = ${esc(p.gpus_required)} GPU${p.gpus_required > 1 ? "s" : ""}</span>
    ${p.profile_type ? `<span class="pill">${esc(p.profile_type)}</span>` : ""}
    ${p.min_vram_gb != null ? `<span class="pill">≥ ${esc(p.min_vram_gb)} GB/GPU</span>` : ""}
    ${p.estimated_vram_gb != null ? `<span class="pill" title="estimated from model size and precision">~${esc(p.estimated_vram_gb)} GB/GPU est.</span>` : ""}
    ${p.feat_lora ? '<span class="pill">LoRA</span>' : ""}
    ${p.nim_optimized ? '<span class="badge ok">nim_optimized</span>' : ""}`;
}

const gpuNameById = (id) => (state.gpus.find((g) => g.id === id) || {}).name || id;

function targetText(p) {
  if (p.match_mode === "gpu_device") {
    const names = (p.gpu_ids || []).map(gpuNameById).join(", ");
    return p.gpu_device_ids.map((d) => `${d}:10de`).join(", ") + (names ? ` (${names})` : "");
  }
  if (p.match_mode === "gpu_name") return p.gpu_names.join(", ") + (p.compute_capability ? ` (CC ${p.compute_capability})` : "");
  if (p.match_mode === "compute_capability") return `CC ${p.compute_capability}`;
  return "any GPU";
}

// Profiles NIM will not auto-select on this GPU but that only fail the hardware tag check
function siblingNote(x, gpuName, hasBest) {
  if (!x.siblings.length) return "";
  const byName = x.siblings.filter((s) => s.sibling_reason === "name");
  const byFamily = x.siblings.filter((s) => s.sibling_reason !== "name");
  const parts = [];
  if (byName.length) {
    parts.push(`<b>Manifest error:</b> ${byName.length > 1 ? "profiles are" : "a profile is"} tagged for ${esc(gpuName)} but
      carr${byName.length > 1 ? "y" : "ies"} the gpu_device of another GPU (${esc([...new Set(byName.map(targetText))].join("; "))}),
      so NIM does not auto-select ${byName.length > 1 ? "them" : "it"}. You can try forcing it:
      <code>NIM_MODEL_PROFILE=${esc(short(byName[0].profile_id))}</code> ${copyBtn(byName[0].profile_id)}
      (verify with <code>list-model-profiles</code>; NIM may still reject the device mismatch).`);
  }
  if (byFamily.length) {
    parts.push(`${hasBest ? "Optimized profiles also exist" : "Profiles exist"} for a sibling SKU of the same GPU family
      (${esc([...new Set(byFamily.map(targetText))].join("; "))}). NIM will not auto-select them on ${esc(gpuName)};
      forcing one with <code>NIM_MODEL_PROFILE</code> is unsupported and may fail.`);
  }
  return `<p class="muted sibling">${parts.join("<br>")}</p>`;
}

function profilesTable(rows, withStatus) {
  if (!rows.length) return '<p class="muted">No profiles.</p>';
  return `<div class="table-wrap"><table class="table"><thead><tr>
    ${withStatus ? "<th>Fit</th>" : ""}<th>Profile id</th><th>Target</th><th>Precision</th><th>Engine</th><th>TP×PP</th>
    <th>Type</th><th>Min VRAM</th><th>Notes</th></tr></thead><tbody>
    ${rows.map((p) => `<tr class="${withStatus && !p.compatible ? "no" : ""}">
      ${withStatus ? `<td>${p.compatible ? '<span class="badge ok">ok</span>' : '<span class="badge bad">no</span>'}</td>` : ""}
      <td class="pid" title="${esc(p.profile_id)}">${esc(short(p.profile_id))} ${copyBtn(p.profile_id, "⧉")}</td>
      <td>${esc(targetText(p))}</td><td>${esc(p.precision || "—")}</td><td>${esc(p.engine || "—")}</td>
      <td>${esc(p.tp)}×${esc(p.pp)}${p.nodes_required > 1 ? ` (${esc(p.nodes_required)} nodes)` : ""}</td>
      <td>${esc(p.profile_type || p.profile_tag || "—")}</td>
      <td>${p.min_vram_gb != null ? esc(p.min_vram_gb) + " GB" : (p.estimated_vram_gb != null ? "~" + esc(p.estimated_vram_gb) + " GB est." : "—")}</td>
      <td>${esc([...(p.blockers || []), ...(p.warnings || []), p.nim_description || ""].filter(Boolean).join(" · "))}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function renderResults() {
  const r = state.rec;
  if (!r) return;
  let rows = r.results;
  if (state.filter === "compatible") rows = rows.filter((x) => x.status === "compatible");
  if (state.filter === "exact") rows = rows.filter((x) => x.fit === 3);
  if (state.filter === "incompatible") rows = rows.filter((x) => x.status !== "compatible");
  if (!rows.length) { $("#rec-results").innerHTML = `<div class="card empty">Nothing to show for this filter.</div>`; return; }
  $("#rec-results").innerHTML = rows.map((x) => {
    const m = x.model;
    const head = `<div class="model-head">
        <h3>${esc(m.display_name)} <small>${esc(m.image)}</small></h3>
        <span class="badge">${esc(m.category)}</span>
        ${x.best ? `<span class="badge ${MATCH_BADGE[x.best.match || x.best.match_mode][0]}">${esc(MATCH_BADGE[x.best.match || x.best.match_mode][1])}</span>`
                 : x.status === "no-profiles" ? `<span class="badge warn">no profiles (${esc(m.fetch_status)})</span>`
                 : '<span class="badge bad">not compatible</span>'}
        <button type="button" class="small" data-model="${esc(m.id)}">all ${esc(m.profile_count)} profiles</button>
      </div>`;
    if (x.best) {
      const b = x.best;
      return `<article class="card model">${head}
        <div class="model-body">
          <div class="best"><b>Best profile</b> <code title="${esc(b.profile_id)}">${esc(short(b.profile_id))}</code> ${copyBtn(b.profile_id)} ${profileLine(b)}</div>
          <ul class="why">${b.why.map((w) => `<li>${esc(w)}</li>`).join("")}${b.warnings.map((w) => `<li class="error">${esc(w)}</li>`).join("")}${b.nim_description ? `<li>NVIDIA: ${esc(b.nim_description)}</li>` : ""}</ul>
          ${siblingNote(x, r.gpu.name, true)}
        </div>
        <details><summary>Deploy &amp; ${esc(x.alternatives.length)} alternatives (${esc(x.compatible_count)} compatible)</summary><div class="inner">
          <b>Kubernetes / PCAI</b> ${copyBtn(b.deploy.kubernetes)}<pre>${esc(b.deploy.kubernetes)}</pre>
          <b>Docker</b> ${copyBtn(b.deploy.docker)}<pre>${esc(b.deploy.docker)}</pre>
          ${x.alternatives.length ? "<b>Alternatives</b>" + profilesTable(x.alternatives, false) : ""}
        </div></details></article>`;
    }
    const reasons = x.blockers.map(([b, n]) => `<li>${esc(b)} <span class="muted">(${esc(n)} profiles)</span></li>`).join("");
    const sib = siblingNote(x, r.gpu.name, false);
    const msg = x.status === "no-profiles" ? `<p class="muted">${esc(m.fetch_message || "Manifest not downloaded.")}</p>` : "";
    return `<article class="card model">${head}<div class="model-body">${msg}${reasons ? `<ul class="blockers">${reasons}</ul>` : ""}${sib}</div></article>`;
  }).join("");
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-model]");
  if (b) openModel(b.dataset.model);
});

/* ---------- model modal ---------- */
const modal = $("#modal");
$("#modal-close").addEventListener("click", () => modal.close());
modal.addEventListener("click", (e) => { if (e.target === modal) modal.close(); });

async function openModel(id) {
  $("#modal-title").textContent = id;
  $("#modal-body").innerHTML = `<span class="spinner"></span>`;
  modal.showModal();
  const req = state.rec && state.rec.request;
  const vram = $("#f-vram").value;
  const qs = req ? `?${new URLSearchParams({ gpu: req.gpu, count: req.count, nodes: req.nodes, goal: req.goal, ...(req.mig ? { mig: req.mig } : {}), ...(vram ? { vram_gb: vram } : {}), lora: req.lora })}` : "";
  try {
    const d = await api(`/api/models/${encodeURIComponent(id)}${qs}`);
    const m = d.model;
    $("#modal-title").textContent = m.display_name;
    const mf = m.manifest || {};
    $("#modal-body").innerHTML = `
      <p>${esc(m.description || "")}</p>
      <div class="kv">
        <div><span>Image (catalog)</span><b class="mono">${esc(m.image)}</b></div>
        <div><span>Fetched from</span><b class="mono">${esc(m.fetched_image)}</b></div>
        <div><span>Category</span><b>${esc(m.category)}</b></div>
        <div><span>Latest version</span><b>${esc(m.latest_version || "—")}</b></div>
        <div><span>Image size</span><b>${fmtBytes(m.size_bytes)}</b></div>
        <div><span>Manifest</span><b>${esc(mf.model || "—")} ${esc(mf.release || "")}</b></div>
        <div><span>Fetch status</span><b><span class="badge ${FETCH_BADGE[m.fetch.status] || ""}">${esc(m.fetch.status)}</span></b></div>
        <div><span>NGC GPU labels</span><b>${esc((m.catalog_gpu_labels || []).join(", ") || "—")}</b></div>
      </div>
      ${m.fetch.message ? `<p class="error">${esc(m.fetch.message)}</p>` : ""}
      <p>${mf.model ? `<a href="/api/models/${encodeURIComponent(m._id)}/manifest" target="_blank" rel="noopener">View model_manifest.yaml</a>` : ""}</p>
      <h3>Profiles ${d.gpu ? `for ${esc(d.gpu.name)} × ${esc(req.count)}` : ""}</h3>
      ${profilesTable(d.profiles, !!d.gpu)}`;
  } catch (e) { $("#modal-body").innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

/* ---------- models tab ---------- */
async function loadModels() {
  $("#m-table").innerHTML = `<tr><td><span class="spinner"></span></td></tr>`;
  state.models = await api("/api/models");
  renderModels();
}
function renderModels() {
  const q = $("#m-q").value.trim().toLowerCase();
  const cat = $("#m-category").value;
  const st = $("#m-status").value;
  const gpuName = (id) => (state.gpus.find((g) => g.id === id) || {}).name || id;
  const rows = state.models.filter((m) => (!cat || m.category === cat) && (!st || m.fetch.status === st) &&
    (!q || [m._id, m.name, m.display_name, m.description, m.image, ...(m.gpu_device_ids || [])].join(" ").toLowerCase().includes(q)));
  $("#m-count").textContent = `${rows.length} of ${state.models.length}`;
  $("#m-table").innerHTML = `<thead><tr><th>NIM</th><th>Category</th><th>Version</th><th class="num">Profiles</th>
    <th>Optimized for</th><th>Fetch</th></tr></thead><tbody>${rows.map((m) => `
    <tr class="clickable" data-model="${esc(m._id)}">
      <td><b>${esc(m.display_name)}</b><div class="muted mono">${esc(m.image)}</div></td>
      <td>${esc(m.category)}</td><td>${esc(m.tag)}${m.manifest && m.manifest.release ? ` <span class="muted">(${esc(m.manifest.release)})</span>` : ""}</td>
      <td class="num">${esc(m.profile_count)}</td>
      <td>${esc((m.gpu_ids || []).map(gpuName).join(", ") || (m.compute_capabilities || []).map((c) => "CC " + c).join(", ") || "generic")}</td>
      <td><span class="badge ${FETCH_BADGE[m.fetch.status] || ""}" title="${esc(m.fetch.message || "")}">${esc(m.fetch.status)}</span>${m.origin === "website" ? ' <span class="badge accent" title="loaded or refreshed from the Load NIMs tab">website</span>' : ""}</td>
    </tr>`).join("")}</tbody>`;
}
["#m-q", "#m-category", "#m-status"].forEach((s) => $(s).addEventListener("input", debounce(renderModels, 150)));

/* ---------- load NIMs tab ---------- */
const load = { config: null, bulk: null, jobs: [], details: {}, open: new Set(), timer: null, watch: null, seen: {} };
const JOB_BADGE = { queued: "info", running: "info", done: "ok", cancelled: "warn", interrupted: "warn" };
const ITEM_BADGE = { queued: "", running: "info", ok: "ok", failed: "bad", skipped: "", cancelled: "warn", interrupted: "warn" };
const ITEM_ORDER = { running: 0, failed: 1, interrupted: 2, cancelled: 3, ok: 4, queued: 5, skipped: 6 };
const STATE_BADGE = {
  new: ["ok", "new"], retry: ["info", "retry"], refresh: ["info", "re-fetch"], skip: ["", "skip"],
  duplicate: ["warn", "duplicate"], invalid: ["bad", "invalid"],
};
const canImport = () => { const c = load.config || {}; return !!(c.enabled && c.finder_available); };

async function loadImportTab() {
  if (!load.config) {
    try { load.config = await api("/api/import/config"); } catch (e) { load.config = { error: e.message }; }
  }
  renderImportStatus();
  refreshJobs();
}

function renderImportStatus() {
  const c = load.config || {};
  const notes = [];
  if (c.error) notes.push(`<span class="error">Cannot read the import configuration: ${esc(c.error)}</span>`);
  if (c.enabled === false) notes.push("Loading NIMs is disabled on this server (<code>IMPORT_ENABLED=false</code>); existing jobs are still shown.");
  if (c.enabled && !c.finder_available) {
    const missing = Object.entries(c.tools || {}).filter(([, ok]) => !ok).map(([t]) => t);
    notes.push(`<code>nim_profile_finder_v2.sh</code> cannot run in this container${missing.length ? ` (missing: ${esc(missing.join(", "))})` : " (script not found)"}.`);
  }
  if (c.enabled && !c.ngc_configured) {
    notes.push(`<b>No NGC API key.</b> NIM images on nvcr.io need one: set <code>NGC_API_KEY</code> in <code>.env</code>
      (docker compose) or <code>env.NGC_API_KEY</code> in the Helm values (PCAI: Configure).`);
  }
  $("#load-status").innerHTML = `<div class="card intro ${notes.length ? "warn-card" : ""}">${notes.length ? notes.join("<br>")
    : `<b>Ready.</b> <span class="muted">NGC key configured · ${esc(c.workers)} images in parallel · up to
       ${esc(Math.round(c.timeout_seconds / 60))} min per image · images under <code>${esc(c.internal_registry_prefix)}</code>
       are fetched from nvcr.io.</span>`}</div>`;
  $("#single-form button[type=submit]").disabled = !canImport();
  updateBulkButton();
}

/* single image */
$("#single-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.currentTarget;
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  $("#single-result").innerHTML = `<p><span class="spinner"></span> Starting…</p>`;
  try {
    const job = await apiPost("/api/import/image", {
      image: f.elements.image.value.trim(),
      display_name: f.elements.display_name.value.trim() || null,
      description: f.elements.description.value.trim() || null,
    });
    load.watch = job._id;
    load.open.add(job._id);
    await refreshJobs();
  } catch (err) {
    $("#single-result").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    btn.disabled = !canImport();
  }
});

const logButton = (jobId, index) => `<button type="button" class="small" data-job-log="${esc(jobId)}" data-index="${esc(index)}">log</button>`;

function renderSingleResult() {
  const d = load.details[load.watch];
  const it = d && d.items[0];
  if (!it) return;
  const box = $("#single-result");
  if (it.status === "queued" || it.status === "running") {
    box.innerHTML = `<p><span class="spinner"></span> ${it.status === "queued" ? "Waiting for a free worker for" : "Reading the manifest of"}
      <code>${esc(it.fetched_image)}</code>…</p>`;
  } else if (it.status === "ok") {
    box.innerHTML = `<p class="ok-text">✓ <b>${esc(it.profiles)}</b> profiles ${it.action === "added" ? "added" : "updated"} for
      <b>${esc(it.name || it.folder)}</b>${it.message ? ` <span class="muted">(${esc(it.message)})</span>` : ""}</p>
      <div class="row-buttons"><button type="button" class="small" data-model="${esc(it.folder)}">View profiles</button>
        <a href="#recommend?q=${encodeURIComponent(it.folder)}">Recommend for a GPU</a> ${it.has_log ? logButton(d._id, it.index) : ""}</div>`;
  } else {
    box.innerHTML = `<p class="error">${esc(it.message || it.status)}</p>${it.has_log ? `<div class="row-buttons">${logButton(d._id, it.index)}</div>` : ""}`;
  }
}

/* bulk catalog JSON */
$("#b-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 20e6) { setBulkError("The file is larger than 20 MB."); return; }
  checkBulk(await file.text(), file.name);
});
$("#b-check").addEventListener("click", () => checkBulk($("#b-text").value, "pasted JSON"));
$("#b-force").addEventListener("change", () => { if (load.bulk) previewBulk(); });

function setBulkError(message) {
  load.bulk = null;
  $("#bulk-preview").innerHTML = `<p class="error">${esc(message)}</p>`;
  updateBulkButton();
}

function checkBulk(text, name) {
  if (!text.trim()) { setBulkError("The JSON is empty."); return; }
  let data;
  try { data = JSON.parse(text); } catch (err) { setBulkError(`Not valid JSON: ${err.message}`); return; }
  load.bulk = { data, name, preview: null };
  previewBulk();
}

async function previewBulk() {
  const b = load.bulk;
  if (!b) return;
  b.preview = null;
  updateBulkButton();
  $("#bulk-preview").innerHTML = `<p><span class="spinner"></span> Checking the catalog…</p>`;
  try {
    b.preview = await apiPost("/api/import/preview", { data: b.data, force: $("#b-force").checked });
    renderBulkPreview();
  } catch (err) {
    setBulkError(err.message);
  }
}

function renderBulkPreview() {
  const b = load.bulk;
  const p = b.preview;
  const c = p.counts || {};
  const chips = Object.entries(STATE_BADGE).filter(([k]) => c[k])
    .map(([k, [cls, label]]) => `<span class="badge ${cls}">${esc(c[k])} ${esc(label)}</span>`).join(" ");
  const rows = [...p.problems.map((x) => ({ ...x, state: "invalid" })), ...p.rows]
    .sort((a, z) => (a.state === "skip") - (z.state === "skip") || a.index - z.index);
  $("#bulk-preview").innerHTML = `<div class="preview">
    <p><b>${esc(b.name)}</b>: ${esc(p.total)} entries, <b>${esc(p.to_fetch)}</b> to fetch</p>
    <div class="chips">${chips}</div>
    <details class="plain"><summary>Show the entries</summary>
      <div class="table-wrap preview-table"><table class="table mini"><thead><tr><th class="num">#</th><th>State</th><th>NIM</th><th>Note</th></tr></thead><tbody>
      ${rows.slice(0, 500).map((r) => `<tr><td class="num">${esc(r.index + 1)}</td>
        <td><span class="badge ${STATE_BADGE[r.state][0]}">${esc(STATE_BADGE[r.state][1])}</span></td>
        <td><b>${esc(r.name || "")}</b><div class="muted mono">${esc(r.image || "")}</div></td><td>${esc(r.message || "")}</td></tr>`).join("")}
      </tbody></table></div>${rows.length > 500 ? `<p class="muted">… and ${esc(rows.length - 500)} more</p>` : ""}
    </details></div>`;
  updateBulkButton();
}

function updateBulkButton() {
  const btn = $("#b-start");
  const p = load.bulk && load.bulk.preview;
  const n = p ? p.to_fetch : 0;
  btn.disabled = !canImport() || !n;
  btn.textContent = p ? (n ? `Start import (${n} image${n > 1 ? "s" : ""})` : "Nothing to fetch") : "Start import";
}

$("#bulk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const b = load.bulk;
  if (!b) return;
  $("#b-start").disabled = true;
  try {
    const job = await apiPost("/api/import/bulk", { data: b.data, force: $("#b-force").checked, source_name: b.name });
    load.open.add(job._id);
    load.bulk = null;
    $("#b-file").value = "";
    $("#bulk-preview").innerHTML = `<p class="ok-text">Import started (job <code>${esc(job._id)}</code>), progress below.</p>`;
    await refreshJobs();
  } catch (err) {
    $("#bulk-preview").insertAdjacentHTML("beforeend", `<p class="error">${esc(err.message)}</p>`);
  } finally {
    updateBulkButton();
  }
});

/* jobs */
async function refreshJobs() {
  clearTimeout(load.timer);
  try {
    load.jobs = await api("/api/import/jobs?limit=20");
  } catch (err) {
    $("#jobs").innerHTML = `<p class="error">${esc(err.message)}</p>`;
    load.timer = setTimeout(refreshJobs, 15000);
    return;
  }
  const ids = load.jobs.map((j) => j._id).filter((id) => load.open.has(id) || id === load.watch);
  const details = await Promise.all(ids.map((id) => api(`/api/import/jobs/${encodeURIComponent(id)}`).catch(() => null)));
  load.details = Object.fromEntries(details.filter(Boolean).map((d) => [d._id, d]));
  // new profiles in the database: refresh the header numbers and the model list
  let changed = false;
  for (const j of load.jobs) {
    const key = `${j.status}/${(j.counts || {}).ok || 0}`;
    if (load.seen[j._id] !== undefined && load.seen[j._id] !== key) changed = true;
    load.seen[j._id] = key;
  }
  if (changed) {
    refreshStats();
    state.models = [];
    if (!$("#tab-models").hidden) loadModels();
  }
  renderJobs();
  renderSingleResult();
  const active = load.jobs.some((j) => j.status === "queued" || j.status === "running");
  $("#jobs-note").textContent = active ? "updating every few seconds" : "";
  if (active) load.timer = setTimeout(refreshJobs, document.hidden ? 10000 : 2500);
}

function countChips(c) {
  return ["ok", "failed", "running", "queued", "skipped", "cancelled", "interrupted"].filter((k) => c[k])
    .map((k) => `<span class="badge ${ITEM_BADGE[k]}">${esc(c[k])} ${esc(k)}</span>`).join(" ");
}

function renderJobs() {
  if (!load.jobs.length) { $("#jobs").innerHTML = `<div class="empty">No import jobs yet.</div>`; return; }
  $("#jobs").innerHTML = load.jobs.map((j) => {
    const c = j.counts || {};
    const finished = ["ok", "failed", "skipped", "cancelled", "interrupted"].reduce((n, k) => n + (c[k] || 0), 0);
    const pct = j.total ? Math.round((100 * finished) / j.total) : 100;
    const active = j.status === "queued" || j.status === "running";
    const retry = !active && canImport() && ["failed", "cancelled", "interrupted"].some((k) => c[k]);
    const open = load.open.has(j._id);
    const d = load.details[j._id];
    return `<div class="job">
      <div class="job-head">
        <span class="badge ${JOB_BADGE[j.status] || ""}">${active ? '<span class="spinner small"></span> ' : ""}${esc(j.status)}</span>
        <b>${j.kind === "single" ? "Single image" : "Bulk import"}</b>
        ${j.source_name ? `<span class="muted">${esc(j.source_name)}</span>` : ""}${j.retry_of ? ' <span class="muted">(retry)</span>' : ""}
        <span class="muted">${esc(fmtTime(j.created_at))}</span>
        <span class="job-counts">${countChips(c)}</span>
        <span class="grow"></span>
        ${active ? `<button type="button" class="small" data-job-cancel="${esc(j._id)}">Cancel</button>` : ""}
        ${retry ? `<button type="button" class="small" data-job-retry="${esc(j._id)}">Retry failed</button>` : ""}
        <button type="button" class="small" data-job-toggle="${esc(j._id)}">${open ? "Hide" : "Details"}</button>
      </div>
      <div class="progress" title="${esc(finished)} of ${esc(j.total)} images"><div style="width:${pct}%"></div></div>
      ${open ? (d ? jobItems(d) : '<p><span class="spinner"></span></p>') : ""}
    </div>`;
  }).join("");
}

function jobItems(d) {
  const items = [...d.items].sort((a, z) => (ITEM_ORDER[a.status] ?? 9) - (ITEM_ORDER[z.status] ?? 9) || a.index - z.index);
  const problems = (d.problems || []).map((p) => `<li>entry ${esc(p.index + 1)}${p.name ? ` (${esc(p.name)})` : ""}: ${esc(p.message)}</li>`).join("");
  return `<div class="table-wrap"><table class="table"><thead><tr><th>Status</th><th>NIM</th><th class="num">Profiles</th>
    <th class="num">Time</th><th>Message</th><th></th></tr></thead><tbody>
    ${items.slice(0, 1000).map((it) => `<tr>
      <td><span class="badge ${ITEM_BADGE[it.status] || ""}">${esc(it.status)}</span>${it.action ? ` <span class="muted">${esc(it.action)}</span>` : ""}</td>
      <td><b>${esc(it.name || it.folder)}</b><div class="muted mono">${esc(it.image)}</div></td>
      <td class="num">${esc(it.profiles ?? "—")}</td>
      <td class="num">${it.seconds != null ? `${esc(Math.round(it.seconds))} s` : ""}</td>
      <td>${esc(it.message || "")}</td>
      <td class="actions">${it.profiles ? `<button type="button" class="small" data-model="${esc(it.folder)}">profiles</button>` : ""}${it.has_log ? logButton(d._id, it.index) : ""}</td>
    </tr>`).join("")}</tbody></table></div>
    ${problems ? `<details class="plain"><summary>${esc(d.problems.length)} invalid entries were not imported</summary><ul>${problems}</ul></details>` : ""}`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-job-toggle],[data-job-cancel],[data-job-retry],[data-job-log]");
  if (!b) return;
  try {
    if (b.dataset.jobToggle) {
      const id = b.dataset.jobToggle;
      if (load.open.has(id)) load.open.delete(id); else load.open.add(id);
      renderJobs();
      if (load.open.has(id)) await refreshJobs();
    } else if (b.dataset.jobCancel) {
      b.disabled = true;
      await apiPost(`/api/import/jobs/${encodeURIComponent(b.dataset.jobCancel)}/cancel`);
      await refreshJobs();
    } else if (b.dataset.jobRetry) {
      b.disabled = true;
      const job = await apiPost(`/api/import/jobs/${encodeURIComponent(b.dataset.jobRetry)}/retry`);
      load.open.add(job._id);
      await refreshJobs();
    } else if (b.dataset.jobLog) {
      $("#modal-title").textContent = "nim_profile_finder_v2.sh output";
      $("#modal-body").innerHTML = `<span class="spinner"></span>`;
      modal.showModal();
      const r = await fetch(`/api/import/jobs/${encodeURIComponent(b.dataset.jobLog)}/items/${encodeURIComponent(b.dataset.index)}/log`);
      $("#modal-body").innerHTML = r.ok ? `<pre class="log">${esc(await r.text())}</pre>` : `<p class="error">No log stored for this image.</p>`;
    }
  } catch (err) {
    $("#jobs-note").innerHTML = `<span class="error">${esc(err.message)}</span>`;
  }
});

/* ---------- validation tab ---------- */
async function loadValidation() {
  $("#v-table").innerHTML = `<tr><td><span class="spinner"></span></td></tr>`;
  const [rows, issues] = await Promise.all([api("/api/validation"), api("/api/validation/issues").catch(() => [])]);
  state.validation = rows;
  renderIssues(issues);
  renderValidation();
}
const ISSUE_BADGE = {
  unknown_device_id: ["bad", "invalid gpu_device"],
  device_name_mismatch: ["warn", "name ≠ gpu_device"],
  unresolved_name: ["info", "unknown gpu name"],
};
function renderIssues(issues) {
  const more = (list, n) => esc(list.slice(0, n).join(", ")) + (list.length > n ? ` <span class="muted">+${list.length - n}</span>` : "");
  $("#v-issues").innerHTML = `<h3>Manifest issues <span class="muted">(${issues.length})</span></h3>` + (issues.length
    ? `<div class="table-wrap"><table class="table"><thead><tr><th>Issue</th><th>gpu_device</th><th>gpu name</th>
        <th class="num">Profiles</th><th>NIMs</th><th>Explanation</th></tr></thead><tbody>${issues.map((i) => `<tr>
        <td><span class="badge ${(ISSUE_BADGE[i.kind] || [""])[0]}">${esc((ISSUE_BADGE[i.kind] || [, i.kind])[1])}</span></td>
        <td><code>${esc(i.gpu_device || "—")}</code></td><td><code>${esc(i.gpu_name)}</code></td>
        <td class="num">${esc(i.profiles)}</td><td>${more(i.models, 4)}</td>
        <td>${esc(i.explanation)}<details><summary>evidence</summary><div class="inner">${(i.locations || [])
          .map((l) => `<div class="src">${esc(l)}</div>`).join("")}</div></details></td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">No manifest issues found.</p>`);
}
function renderValidation() {
  const kind = $("#v-kind").value;
  const st = $("#v-status").value;
  const rows = state.validation.filter((v) => v.kind === kind && (!st || v.status === st));
  $("#v-count").textContent = `${rows.length} values`;
  const co = (v) => (v.cooccurrence || []).map((c) => {
    const label = c.name || c.device;
    const mark = c.agrees === true ? "✓" : c.agrees === false ? "✗" : "?";
    return `<span class="badge ${c.agrees === true ? "ok" : c.agrees === false ? "warn" : ""}">${mark} ${esc(label)} ×${esc(c.profiles)}</span>`;
  }).join(" ");
  const research = (v) => {
    const r = v.research;
    if (!r) return "";
    const src = (r.sources || []).slice(0, 3).map((s) => `<div class="src">${esc(s)}</div>`).join("");
    return `<div><span class="badge ${r.verdict === "confirmed" ? "ok" : r.verdict === "refuted" ? "bad" : "info"}">research: ${esc(r.verdict || "?")}</span> ${esc(r.summary || "")}</div>${src}`;
  };
  $("#v-table").innerHTML = `<thead><tr><th>${kind === "gpu_device" ? "gpu_device" : kind === "gpu_name" ? "Internal gpu name" : "Compute capability"}</th>
    <th>Maps to GPU</th><th>Status</th><th class="num">Profiles</th><th class="num">NIMs</th>
    <th>${kind === "gpu_device" ? "Internal names seen with it" : kind === "gpu_name" ? "Device ids seen with it" : ""}</th><th>Evidence / notes</th></tr></thead>
    <tbody>${rows.map((v) => `<tr>
      <td><code>${esc(v.value)}</code></td>
      <td>${esc((v.gpu_names || []).join(", ") || "—")}</td>
      <td><span class="badge ${STATUS_BADGE[v.status]}">${esc(v.status)}</span></td>
      <td class="num">${esc(v.profile_count)}</td><td class="num">${esc(v.model_count)}</td>
      <td>${co(v)}</td>
      <td>${research(v)}${(v.notes || []).map((n) => `<div class="muted">${esc(n)}</div>`).join("")}</td>
    </tr>`).join("")}</tbody>`;
}
["#v-kind", "#v-status"].forEach((s) => $(s).addEventListener("input", renderValidation));

/* ---------- PCAI tab ---------- */
function renderPcai() {
  const opts = (list, v) => (list && list.length > 1 ? list.join(" or ") : (v ?? "—"));
  const mig = (c) => ((c.mig_partitions || []).length ? `<table class="table mini"><thead><tr><th>Partition</th>
      <th>MIG profile</th><th class="num">per GPU</th><th class="num">per node</th></tr></thead><tbody>${c.mig_partitions.map((m) => `<tr>
      <td>${esc(m.size)}</td><td>${m.profile ? `<code>${esc(m.profile)}</code>` : "full GPU"}</td>
      <td class="num">${esc(m.per_gpu)}</td><td class="num">${esc(m.per_node)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">No MIG support.</p>`);
  $("#pcai-list").innerHTML = state.pcai.map((c) => `<div class="card pcai-card">
    <h3>${esc(c.name)}</h3>
    <div>${c.generation ? `<span class="badge ${c.generation === "G2" ? "accent" : ""}">${esc(c.generation)}</span>` : ""}
      <span class="badge ${c.status === "current" ? "ok" : "warn"}">${esc(c.status || "status ?")}</span>
      <span class="muted">${esc(c.size)}</span></div>
    <div class="kv">
      <div><span>GPU</span><b>${esc(c.gpu ? c.gpu.name : c.gpu_model)}</b></div>
      <div><span>gpu_device</span><b class="mono">${esc(c.gpu ? c.gpu.nim_gpu_device.join(", ") : "—")}</b></div>
      <div><span>GPUs / worker node</span><b>${esc(c.gpus_per_worker_node)}</b></div>
      <div><span>GPU worker nodes</span><b>${esc(opts(c.worker_node_options, c.gpu_worker_nodes))}</b></div>
      <div><span>Total GPUs</span><b>${esc(opts(c.total_gpu_options, c.total_gpus))}</b></div>
      <div><span>Memory / GPU</span><b>${fmtGB(c.gpu && c.gpu.vram_gb)}</b></div>
    </div>
    ${c.server_platform ? `<p class="muted">Platform: ${esc(c.server_platform)}</p>` : ""}
    ${c.storage ? `<p class="muted">Storage: ${esc(c.storage)} · Network: ${esc(c.ai_network || "—")}</p>` : ""}
    ${c.expansion ? `<p class="muted">Expansion: ${esc(c.expansion)}</p>` : ""}
    ${mig(c)}
    ${c.nvlink ? `<p class="muted">NVLink: ${esc(c.nvlink)}</p>` : ""}
    ${c.notes ? `<p class="muted">${esc(c.notes)}</p>` : ""}
    <button type="button" class="primary" data-use-pcai="${esc(c._id)}">Use in recommender</button>
    <details><summary>Sources</summary><div class="inner">${(c.sources || []).map((s) => `<div class="src">${esc(s)}</div>`).join("")}</div></details>
  </div>`).join("") || `<div class="card empty">No PCAI configurations imported.</div>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-use-pcai]");
  if (!b) return;
  location.hash = "#recommend";
  $("#f-pcai").value = b.dataset.usePcai;
  $("#f-pcai").dispatchEvent(new Event("change"));
});

/* ---------- about tab ---------- */
let aboutLoaded = false;
async function renderAbout() {
  if (aboutLoaded) return;
  aboutLoaded = true;
  const rules = await api("/api/rules").catch(() => ({}));
  const list = (items, f) => `<ul>${(items || []).map((x) => `<li>${f(x)}</li>`).join("")}</ul>`;
  const s = stats();
  const counts = (s.import_run && s.import_run.counts) || {};
  $("#about").innerHTML = `
    <h2>How a profile is chosen</h2>
    <ol>
      <li><b>Resolve the GPU.</b> Any name, Kubernetes <code>nvidia.com/gpu.product</code> label, NIM internal name
        (e.g. <code>h200-nvl</code>) or PCI id (e.g. <code>233b</code>) is resolved to one GPU of the reference catalog,
        which carries its PCI device ids, memory and compute capability.</li>
      <li><b>Hardware match.</b> Profiles with a <code>gpu_device</code> tag only run where the PCI device id matches
        (this is how NIM decides compatibility). Profiles with an internal <code>gpu</code> name must list the GPU; when
        they also carry a compute capability (Maxine / speech engines), a GPU with the same compute capability is accepted
        with a warning. <code>compute_capability</code>-only engines need the same compute capability. Generic profiles run
        on any GPU whose architecture supports their precision (bf16 ≥ CC 8.0, fp8 ≥ 8.9, nvfp4 ≥ 10.0).</li>
      <li><b>Resources.</b> tp × pp (or <code>n_gpus</code>) must fit the GPUs per node, multi-node profiles need enough
        nodes, and <code>min_vram_per_device_gb</code> must fit the GPU (or MIG slice) memory. For generic LLM profiles
        without a memory tag, weight memory is estimated from the parameter count in the model name.</li>
      <li><b>Ranking.</b> Exact hardware match → NVIDIA-optimized engine → efficient precision → non-LoRA →
        profile type → GPU count, adjustable with <i>Optimize for</i>.</li>
    </ol>
    ${rules.selection_preference_order ? `<h2>NVIDIA NIM selection rules (research)</h2>${list(rules.selection_preference_order, (r) => `${esc(r.rule)} <div class="src">${esc(r.source)}</div>`)}` : ""}
    ${rules.compatibility_checks ? `<h2>Compatibility checks</h2>${list(rules.compatibility_checks, (r) => `${esc(r.rule)} <div class="src">${esc(r.source)}</div>`)}` : ""}
    ${rules.precision_by_architecture ? `<h2>Precision support</h2>${list(rules.precision_by_architecture, (r) => `<b>${esc(r.precision)}</b>: compute capability ≥ ${esc(r.min_compute_capability)} ${esc(r.architectures || "")} ${esc(r.notes || "")}`)}` : ""}
    ${rules.caveats ? `<h2>Caveats</h2>${list(rules.caveats, (c) => esc(c))}` : ""}
    <h2>Data</h2>
    <ul>
      <li>Catalog entries: ${esc(counts.catalog_entries ?? "?")} · models with profiles: ${esc(counts.models_with_profiles ?? "?")} · profiles: ${esc(counts.profiles ?? "?")}</li>
      <li>Fetch status: ${esc(JSON.stringify(counts.fetch_status || {}))}</li>
      <li>NIMs loaded from the website (<a href="#load">Load NIMs</a>): ${esc(counts.website_additions ?? 0)}</li>
      <li>Validation: ${esc(JSON.stringify(counts.validation_status || {}))}</li>
      ${((s.import_run && s.import_run.warnings) || []).map((w) => `<li class="error">${esc(w)}</li>`).join("")}
    </ul>`;
}

init();
