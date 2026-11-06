# NIM gpu_device / internal GPU name validation

Generated 2026-11-05 by `nim_advisor/tools/validate_gpu_ids.py` from `nims_catalog/*/model_manifest.yaml`.
Primary sources: NVIDIA driver 595.80 README Appendix A (https://download.nvidia.com/XFree86/Linux-x86_64/595.80/README/supportedchips.html); pci.ids 2026.11.05 (https://pci-ids.ucw.cz/v2.2/pci.ids).

- gpu_device ids: 39 ({'confirmed': 36, 'refuted': 3})
- internal gpu names: 115 ({'confirmed': 110, 'unresolved': 1, 'inferred': 4})
- manifest issues: {'device_name_mismatch': 2, 'unknown_device_id': 3, 'unresolved_name': 1}

## Manifest issues

| Kind | gpu_device | gpu name | Profiles | NIMs | Explanation |
| --- | --- | --- | ---: | --- | --- |
| device_name_mismatch | 232c:10de | H20 | 2 | qwen3-32b_1.0.0 | gpu 'H20' (NVIDIA H20) is written with gpu_device 232c:10de, which is NVIDIA H20-3e. NIM matches gpu_device, so these profiles are selected on NVIDIA H20-3e, not on NVIDIA H20. |
| device_name_mismatch | 26b5:10de | L40S | 45 | codellama-13b-instruct_1.2.2, codellama-34b-instruct_latest, llama-2-13b-chat_1.0.3, llama-2-70b-chat_1.0.3, llama-2-7b-chat_1.0.3, llama-3-sqlcoder-8b_latest ... | gpu 'L40S' (NVIDIA L40S) is written with gpu_device 26b5:10de, which is NVIDIA L40. NIM matches gpu_device, so these profiles are selected on NVIDIA L40, not on NVIDIA L40S. |
| unknown_device_id | 2e01:10de | RTX5080 | 4 | audio2face-3d_latest | gpu 'RTX5080' is written with gpu_device 2e01:10de, which is not an NVIDIA device id; NVIDIA GeForce RTX 5080 is 2c02:10de. NIM cannot match these profiles on any GPU. |
| unknown_device_id | 2e02:10de | RTX5090 | 4 | audio2face-3d_latest | gpu 'RTX5090' is written with gpu_device 2e02:10de, which is 'GB20B [JMJWOA-Generic-GPU]' in pci.ids; NVIDIA GeForce RTX 5090 is 2b85:10de. NIM cannot match these profiles on any GPU. |
| unknown_device_id | 2e14:10de | rtx6000-blackwell-sv | 4 | audio2face-3d_latest | gpu 'rtx6000-blackwell-sv' is written with gpu_device 2e14:10de, which is not an NVIDIA device id; NVIDIA RTX PRO 6000 Blackwell Server Edition is 2bb5:10de. NIM cannot match these profiles on any GPU. |
| unresolved_name | - | B40 | 3 | active-speaker-detection-h4m-nim_1.0.0, ai4m-relighting_latest, studio-voice-h4m_1.2.0 | 'B40' is not an NVIDIA product name in the driver README / pci.ids and has no gpu_device next to it; its profiles are matched by compute capability 12.0. |

## gpu_device ids

| gpu_device | Verdict | GPU | Driver README name | pci.ids name | Profiles | NIMs |
| --- | --- | --- | --- | --- | ---: | ---: |
| `20b0:10de` | confirmed | NVIDIA A100-SXM4-40GB | NVIDIA A100-SXM4-40GB / NVIDIA A100-PG509-200 | GA100 [A100 SXM4 40GB] | 29 | 16 |
| `20b2:10de` | confirmed | NVIDIA A100-SXM4-80GB | NVIDIA A100-SXM4-80GB / NVIDIA PG509-210 | GA100 [A100 SXM4 80GB] | 191 | 71 |
| `20b5:10de` | confirmed | NVIDIA A100 80GB PCIe | NVIDIA A100 80GB PCIe | GA100 [A100 PCIe 80GB] | 4 | 2 |
| `20b7:10de` | confirmed | NVIDIA A30 | NVIDIA A30 | GA100GL [A30 PCIe] | 4 | 1 |
| `20f1:10de` | confirmed | NVIDIA A100-PCIE-40GB | NVIDIA A100-PCIE-40GB | GA100 [A100 PCIe 40GB] | 3 | 1 |
| `2237:10de` | confirmed | NVIDIA A10G | NVIDIA A10G | GA102GL [A10G] | 54 | 30 |
| `230e:10de` | confirmed | NVIDIA H20 NVL16 | NVIDIA H20 NVL16 | GH100 [H20 NVL16] | 1 | 1 |
| `2321:10de` | confirmed | NVIDIA H100 NVL | NVIDIA H100 NVL | GH100 [H100L 94GB] | 92 | 32 |
| `2329:10de` | confirmed | NVIDIA H20 | NVIDIA H20 | GH100 [H20] | 43 | 21 |
| `232c:10de` | confirmed | NVIDIA H20-3e | NVIDIA H20-3e | GH100 [H20 HBM3e] | 29 | 14 |
| `2330:10de` | confirmed | NVIDIA H100 SXM5 80GB | NVIDIA H100 80GB HBM3 | GH100 [H100 SXM5 80GB] | 281 | 91 |
| `2331:10de` | confirmed | NVIDIA H100 PCIe 80GB | NVIDIA H100 PCIe | GH100 [H100 PCIe] | 34 | 14 |
| `2335:10de` | confirmed | NVIDIA H200 SXM 141GB | NVIDIA H200 | GH100 [H200 SXM 141GB] | 174 | 69 |
| `233b:10de` | confirmed | NVIDIA H200 NVL | NVIDIA H200 NVL | GH100 [H200 NVL] | 54 | 18 |
| `2342:10de` | confirmed | NVIDIA GH200 480GB / 120GB | NVIDIA GH200 120GB / NVIDIA GH200 480GB | GH100 [GH200 120GB / 480GB] | 34 | 15 |
| `2348:10de` | confirmed | NVIDIA GH200 144G HBM3e | NVIDIA GH200 144G HBM3e | GH100 [GH200 144G HBM3e] | 56 | 17 |
| `2684:10de` | confirmed | NVIDIA GeForce RTX 4090 | NVIDIA GeForce RTX 4090 | AD102 [GeForce RTX 4090] | 35 | 10 |
| `26b1:10de` | confirmed | NVIDIA RTX 6000 Ada Generation | NVIDIA RTX 6000 Ada Generation | AD102GL [RTX 6000 Ada Generation] | 35 | 10 |
| `26b5:10de` | confirmed | NVIDIA L40 | NVIDIA L40 | AD102GL [L40] | 49 | 20 |
| `26b9:10de` | confirmed | NVIDIA L40S | NVIDIA L40S | AD102GL [L40S] | 144 | 45 |
| `26ba:10de` | confirmed | NVIDIA L20 | NVIDIA L20 | AD102GL [L20] | 15 | 6 |
| `2704:10de` | confirmed | NVIDIA GeForce RTX 4080 | NVIDIA GeForce RTX 4080 | AD103 [GeForce RTX 4080] | 2 | 2 |
| `27b1:10de` | confirmed | NVIDIA RTX 4500 Ada Generation | NVIDIA RTX 4500 Ada Generation | AD104GL [RTX 4500 Ada Generation] | 2 | 1 |
| `27b8:10de` | confirmed | NVIDIA L4 | NVIDIA L4 | AD104GL [L4] | 10 | 5 |
| `2901:10de` | confirmed | NVIDIA B200 | NVIDIA B200 | GB100 [B200] | 143 | 57 |
| `2941:10de` | confirmed | NVIDIA GB200 | NVIDIA GB200 | GB100 [HGX GB200] | 58 | 18 |
| `2b85:10de` | confirmed | NVIDIA GeForce RTX 5090 | NVIDIA GeForce RTX 5090 | GB202 [GeForce RTX 5090] | 23 | 7 |
| `2bb1:10de` | confirmed | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | GB202GL [RTX PRO 6000 Blackwell Workstation Edition] | 40 | 12 |
| `2bb4:10de` | confirmed | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | GB202GL [RTX PRO 6000 Blackwell Max-Q Workstation Edition] | 6 | 3 |
| `2bb5:10de` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | NVIDIA RTX PRO 6000 Blackwell Server Edition | GB202GL [RTX PRO 6000 Blackwell Server Edition] | 77 | 22 |
| `2c02:10de` | confirmed | NVIDIA GeForce RTX 5080 | NVIDIA GeForce RTX 5080 | GB203 [GeForce RTX 5080] | 23 | 7 |
| `2c31:10de` | confirmed | NVIDIA RTX PRO 4500 Blackwell | NVIDIA RTX PRO 4500 Blackwell | GB203GL [RTX PRO 4500 Blackwell] | 2 | 1 |
| `2c3a:10de` | confirmed | NVIDIA RTX PRO 4500 Blackwell Server Edition | NVIDIA RTX PRO 4500 Blackwell Server Edition | GB203GL [RTX PRO 4500 Blackwell Server Edition] | 8 | 5 |
| `2e01:10de` | refuted | - | - | - | 4 | 1 |
| `2e02:10de` | refuted | - | - | GB20B [JMJWOA-Generic-GPU] | 4 | 1 |
| `2e12:10de` | confirmed | NVIDIA GB10 (DGX Spark) | NVIDIA GB10 | GB20B [GB10] | 65 | 17 |
| `2e14:10de` | refuted | - | - | - | 4 | 1 |
| `3182:10de` | confirmed | NVIDIA B300 SXM6 AC | NVIDIA B300 SXM6 AC | GB110 [B300 SXM6 AC] | 29 | 12 |
| `31c2:10de` | confirmed | NVIDIA GB300 | NVIDIA GB300 | GB110 [GB300] | 20 | 10 |

## Internal gpu names

| Name | Verdict | GPU | Basis | Profiles | Evidence |
| --- | --- | --- | --- | ---: | --- |
| `5080-wsl` | confirmed | NVIDIA GeForce RTX 5080 | gpu_device co-occurrence | 1 | '5080-wsl' = NVIDIA GeForce RTX 5080 (gpu_device co-occurrence). Paired with 2c02:10de x1. |
| `5090-wsl` | confirmed | NVIDIA GeForce RTX 5090 | gpu_device co-occurrence | 1 | '5090-wsl' = NVIDIA GeForce RTX 5090 (gpu_device co-occurrence). Paired with 2b85:10de x1. |
| `A10` | confirmed | NVIDIA A10 | official nvidia-smi / Kubernetes product name | 8 | 'A10' = NVIDIA A10 (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 8.6. |
| `a100` | confirmed | NVIDIA A100-SXM4-80GB | gpu_device co-occurrence | 102 | 'a100' = NVIDIA A100-SXM4-80GB (gpu_device co-occurrence). Paired with 20b2:10de x102. |
| `A100` | confirmed | NVIDIA A100-SXM4-80GB | gpu_device co-occurrence | 56 | 'A100' = NVIDIA A100-SXM4-80GB (gpu_device co-occurrence). Paired with 20b2:10de x55. |
| `A100-PCIe` | confirmed | NVIDIA A100 80GB PCIe | gpu_device co-occurrence | 4 | 'A100-PCIe' = NVIDIA A100 80GB PCIe (gpu_device co-occurrence). Paired with 20b5:10de x4. |
| `A100-PCIE-40GB` | confirmed | NVIDIA A100-PCIE-40GB | official nvidia-smi / Kubernetes product name | 3 | 'A100-PCIE-40GB' = NVIDIA A100-PCIE-40GB (official nvidia-smi / Kubernetes product name). Paired with 20f1:10de x3. |
| `a100-sxm4-40gb` | confirmed | NVIDIA A100-SXM4-40GB | official nvidia-smi / Kubernetes product name | 7 | 'a100-sxm4-40gb' = NVIDIA A100-SXM4-40GB (official nvidia-smi / Kubernetes product name). Paired with 20b0:10de x3. |
| `a100-sxm4-80gb` | confirmed | NVIDIA A100-SXM4-80GB | official nvidia-smi / Kubernetes product name | 8 | 'a100-sxm4-80gb' = NVIDIA A100-SXM4-80GB (official nvidia-smi / Kubernetes product name). Paired with 20b2:10de x3. |
| `a100_sxm4_40gb` | confirmed | NVIDIA A100-SXM4-40GB | official nvidia-smi / Kubernetes product name | 21 | 'a100_sxm4_40gb' = NVIDIA A100-SXM4-40GB (official nvidia-smi / Kubernetes product name). Paired with 20b0:10de x21. |
| `a10g` | confirmed | NVIDIA A10G | official nvidia-smi / Kubernetes product name | 28 | 'a10g' = NVIDIA A10G (official nvidia-smi / Kubernetes product name). Paired with 2237:10de x23. |
| `A10G` | confirmed | NVIDIA A10G | official nvidia-smi / Kubernetes product name | 28 | 'A10G' = NVIDIA A10G (official nvidia-smi / Kubernetes product name). Paired with 2237:10de x28. |
| `A16` | confirmed | NVIDIA A16 | official nvidia-smi / Kubernetes product name | 2 | 'A16' = NVIDIA A16 (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 8.6. |
| `A2` | confirmed | NVIDIA A2 | official nvidia-smi / Kubernetes product name | 2 | 'A2' = NVIDIA A2 (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 8.6. |
| `A30` | confirmed | NVIDIA A30 | official nvidia-smi / Kubernetes product name | 10 | 'A30' = NVIDIA A30 (official nvidia-smi / Kubernetes product name). Paired with 20b7:10de x4. |
| `A40` | confirmed | NVIDIA A40 | official nvidia-smi / Kubernetes product name | 3 | 'A40' = NVIDIA A40 (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 8.6. |
| `B100` | confirmed | NVIDIA B100 | alias | 1 | 'B100' = NVIDIA B100 (alias). No gpu_device: engines selected by compute capability 10.0. |
| `B200` | confirmed | NVIDIA B200 | official nvidia-smi / Kubernetes product name | 32 | 'B200' = NVIDIA B200 (official nvidia-smi / Kubernetes product name). Paired with 2901:10de x31. |
| `b200` | confirmed | NVIDIA B200 | official nvidia-smi / Kubernetes product name | 77 | 'b200' = NVIDIA B200 (official nvidia-smi / Kubernetes product name). Paired with 2901:10de x73. |
| `b300` | confirmed | NVIDIA B300 SXM6 AC | alias | 16 | 'b300' = NVIDIA B300 SXM6 AC (alias). Paired with 3182:10de x16. |
| `B40` | unresolved | - | - | 3 | 'B40' is not an NVIDIA product name in the driver README / pci.ids and has no gpu_device next to it; its profiles are matched by compute capability 12.0. |
| `DGX Spark` | confirmed | NVIDIA GB10 (DGX Spark) | alias | 1 | 'DGX Spark' = NVIDIA GB10 (DGX Spark) (alias). |
| `dgx-spark` | confirmed | NVIDIA GB10 (DGX Spark) | alias | 4 | 'dgx-spark' = NVIDIA GB10 (DGX Spark) (alias). Paired with 2e12:10de x4. |
| `dgx_spark` | confirmed | NVIDIA GB10 (DGX Spark) | alias | 15 | 'dgx_spark' = NVIDIA GB10 (DGX Spark) (alias). Paired with 2e12:10de x15. |
| `GA102` | inferred | NVIDIA A10, NVIDIA A10G, NVIDIA A40, NVIDIA GeForce RTX 3080, NVIDIA RTX A6000 | chip family GA102 (compute capability 8.6) | 1 | 'GA102' = NVIDIA A10, NVIDIA A10G, NVIDIA A40, NVIDIA GeForce RTX 3080, NVIDIA RTX A6000 (chip family GA102 (compute capability 8.6)). No gpu_device: engines selected by compute capability 8.6. |
| `GA104` | inferred | NVIDIA GeForce RTX 3070 | chip family GA104 (compute capability 8.6) | 1 | 'GA104' = NVIDIA GeForce RTX 3070 (chip family GA104 (compute capability 8.6)). No gpu_device: engines selected by compute capability 8.6. |
| `gb10` | confirmed | NVIDIA GB10 (DGX Spark) | official nvidia-smi / Kubernetes product name | 24 | 'gb10' = NVIDIA GB10 (DGX Spark) (official nvidia-smi / Kubernetes product name). Paired with 2e12:10de x20. |
| `GB10` | confirmed | NVIDIA GB10 (DGX Spark) | official nvidia-smi / Kubernetes product name | 1 | 'GB10' = NVIDIA GB10 (DGX Spark) (official nvidia-smi / Kubernetes product name). Paired with 2e12:10de x1. |
| `GB20` | inferred | NVIDIA GeForce RTX 5080, NVIDIA GeForce RTX 5090, NVIDIA RTX PRO 4500 Blackwell, NVIDIA RTX PRO 4500 Blackwell Server Edition, NVIDIA RTX PRO 5000 Blackwell, NVIDIA RTX PRO 6000 Blackwell Server Edition, NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, NVIDIA RTX PRO 6000 Blackwell Workstation Edition | chip family GB20 (compute capability 12.0) | 1 | 'GB20' = NVIDIA GeForce RTX 5080, NVIDIA GeForce RTX 5090, NVIDIA RTX PRO 4500 Blackwell, NVIDIA RTX PRO 4500 Blackwell Server Edition, NVIDIA RTX PRO 5000 Blackwell, NVIDIA RTX PRO 6000 Blackwell Server Edition, NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, NVIDIA RTX PRO 6000 Blackwell Workstation Edition (chip family GB20 (compute capability 12.0)). No gpu_device: engines selected by compute capability 12.0. |
| `gb200` | confirmed | NVIDIA GB200 | official nvidia-smi / Kubernetes product name | 45 | 'gb200' = NVIDIA GB200 (official nvidia-smi / Kubernetes product name). Paired with 2941:10de x45. |
| `GB200` | confirmed | NVIDIA GB200 | official nvidia-smi / Kubernetes product name | 1 | 'GB200' = NVIDIA GB200 (official nvidia-smi / Kubernetes product name). Paired with 2941:10de x1. |
| `gb300` | confirmed | NVIDIA GB300, NVIDIA GB300 Desktop (DGX Station) | official nvidia-smi / Kubernetes product name | 4 | 'gb300' = NVIDIA GB300, NVIDIA GB300 Desktop (DGX Station) (official nvidia-smi / Kubernetes product name). Paired with 31c2:10de x4. |
| `gh200` | confirmed | NVIDIA GH200 144G HBM3e, NVIDIA GH200 480GB / 120GB | gpu_device co-occurrence | 20 | 'gh200' = NVIDIA GH200 144G HBM3e, NVIDIA GH200 480GB / 120GB (gpu_device co-occurrence). Paired with 2342:10de x8, 2348:10de x12. |
| `gh200-144gb` | confirmed | NVIDIA GH200 144G HBM3e | alias | 6 | 'gh200-144gb' = NVIDIA GH200 144G HBM3e (alias). Paired with 2348:10de x6. |
| `gh200-480gb` | confirmed | NVIDIA GH200 480GB / 120GB | official nvidia-smi / Kubernetes product name | 1 | 'gh200-480gb' = NVIDIA GH200 480GB / 120GB (official nvidia-smi / Kubernetes product name). Paired with 2342:10de x1. |
| `gh200_144gb` | confirmed | NVIDIA GH200 144G HBM3e | alias | 32 | 'gh200_144gb' = NVIDIA GH200 144G HBM3e (alias). Paired with 2348:10de x32. |
| `gh200_480gb` | confirmed | NVIDIA GH200 480GB / 120GB | official nvidia-smi / Kubernetes product name | 19 | 'gh200_480gb' = NVIDIA GH200 480GB / 120GB (official nvidia-smi / Kubernetes product name). Paired with 2342:10de x19. |
| `h100` | confirmed | NVIDIA H100 SXM5 80GB | gpu_device co-occurrence | 126 | 'h100' = NVIDIA H100 SXM5 80GB (gpu_device co-occurrence). Paired with 2330:10de x126. |
| `H100` | confirmed | NVIDIA H100 SXM5 80GB | gpu_device co-occurrence | 126 | 'H100' = NVIDIA H100 SXM5 80GB (gpu_device co-occurrence). Paired with 2330:10de x120. |
| `h100-hbm3-80gb` | confirmed | NVIDIA H100 SXM5 80GB | gpu_device co-occurrence | 12 | 'h100-hbm3-80gb' = NVIDIA H100 SXM5 80GB (gpu_device co-occurrence). Paired with 2330:10de x3. |
| `H100-NVL` | confirmed | NVIDIA H100 NVL | official nvidia-smi / Kubernetes product name | 8 | 'H100-NVL' = NVIDIA H100 NVL (official nvidia-smi / Kubernetes product name). Paired with 2321:10de x8. |
| `h100-nvl` | confirmed | NVIDIA H100 NVL | official nvidia-smi / Kubernetes product name | 29 | 'h100-nvl' = NVIDIA H100 NVL (official nvidia-smi / Kubernetes product name). Paired with 2321:10de x21. |
| `H100-PCIe` | confirmed | NVIDIA H100 PCIe 80GB | official nvidia-smi / Kubernetes product name | 15 | 'H100-PCIe' = NVIDIA H100 PCIe 80GB (official nvidia-smi / Kubernetes product name). Paired with 2331:10de x15. |
| `h100-pcie` | confirmed | NVIDIA H100 PCIe 80GB | official nvidia-smi / Kubernetes product name | 18 | 'h100-pcie' = NVIDIA H100 PCIe 80GB (official nvidia-smi / Kubernetes product name). Paired with 2331:10de x18. |
| `h100_nvl` | confirmed | NVIDIA H100 NVL | official nvidia-smi / Kubernetes product name | 52 | 'h100_nvl' = NVIDIA H100 NVL (official nvidia-smi / Kubernetes product name). Paired with 2321:10de x52. |
| `h20` | confirmed | NVIDIA H20 | official nvidia-smi / Kubernetes product name | 22 | 'h20' = NVIDIA H20 (official nvidia-smi / Kubernetes product name). Paired with 2329:10de x20. |
| `H20` | confirmed | NVIDIA H20 | official nvidia-smi / Kubernetes product name | 25 | 'H20' = NVIDIA H20 (official nvidia-smi / Kubernetes product name). Paired with 2329:10de x23. Manifest error: 232c:10de x2 is NVIDIA H20-3e. |
| `H20-3e` | confirmed | NVIDIA H20-3e | official nvidia-smi / Kubernetes product name | 27 | 'H20-3e' = NVIDIA H20-3e (official nvidia-smi / Kubernetes product name). Paired with 232c:10de x27. |
| `H200` | confirmed | NVIDIA H200 SXM 141GB | official nvidia-smi / Kubernetes product name | 60 | 'H200' = NVIDIA H200 SXM 141GB (official nvidia-smi / Kubernetes product name). Paired with 2335:10de x60. |
| `h200` | confirmed | NVIDIA H200 SXM 141GB | official nvidia-smi / Kubernetes product name | 73 | 'h200' = NVIDIA H200 SXM 141GB (official nvidia-smi / Kubernetes product name). Paired with 2335:10de x73. |
| `h200-nvl` | confirmed | NVIDIA H200 NVL | official nvidia-smi / Kubernetes product name | 17 | 'h200-nvl' = NVIDIA H200 NVL (official nvidia-smi / Kubernetes product name). Paired with 233b:10de x13. |
| `H200-NVL` | confirmed | NVIDIA H200 NVL | official nvidia-smi / Kubernetes product name | 1 | 'H200-NVL' = NVIDIA H200 NVL (official nvidia-smi / Kubernetes product name). Paired with 233b:10de x1. |
| `h200_nvl` | confirmed | NVIDIA H200 NVL | official nvidia-smi / Kubernetes product name | 30 | 'h200_nvl' = NVIDIA H200 NVL (official nvidia-smi / Kubernetes product name). Paired with 233b:10de x30. |
| `l20` | confirmed | NVIDIA L20 | official nvidia-smi / Kubernetes product name | 10 | 'l20' = NVIDIA L20 (official nvidia-smi / Kubernetes product name). Paired with 26ba:10de x8. |
| `L20` | confirmed | NVIDIA L20 | official nvidia-smi / Kubernetes product name | 7 | 'L20' = NVIDIA L20 (official nvidia-smi / Kubernetes product name). Paired with 26ba:10de x7. |
| `L4` | confirmed | NVIDIA L4 | official nvidia-smi / Kubernetes product name | 7 | 'L4' = NVIDIA L4 (official nvidia-smi / Kubernetes product name). Paired with 27b8:10de x4. |
| `l4` | confirmed | NVIDIA L4 | official nvidia-smi / Kubernetes product name | 11 | 'l4' = NVIDIA L4 (official nvidia-smi / Kubernetes product name). Paired with 27b8:10de x6. |
| `L40` | confirmed | NVIDIA L40 | official nvidia-smi / Kubernetes product name | 8 | 'L40' = NVIDIA L40 (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 8.9. |
| `l40` | confirmed | NVIDIA L40 | official nvidia-smi / Kubernetes product name | 4 | 'l40' = NVIDIA L40 (official nvidia-smi / Kubernetes product name). Paired with 26b5:10de x4. |
| `L40S` | confirmed | NVIDIA L40S | official nvidia-smi / Kubernetes product name | 57 | 'L40S' = NVIDIA L40S (official nvidia-smi / Kubernetes product name). Paired with 26b9:10de x11. Manifest error: 26b5:10de x45 is NVIDIA L40. |
| `l40s` | confirmed | NVIDIA L40S | official nvidia-smi / Kubernetes product name | 119 | 'l40s' = NVIDIA L40S (official nvidia-smi / Kubernetes product name). Paired with 26b9:10de x110. |
| `NVIDIA A100-SXM4-40GB` | confirmed | NVIDIA A100-SXM4-40GB | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA A100-SXM4-40GB' = NVIDIA A100-SXM4-40GB (official nvidia-smi / Kubernetes product name). Paired with 20b0:10de x3. |
| `NVIDIA A100-SXM4-80GB` | confirmed | NVIDIA A100-SXM4-80GB | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA A100-SXM4-80GB' = NVIDIA A100-SXM4-80GB (official nvidia-smi / Kubernetes product name). Paired with 20b2:10de x3. |
| `NVIDIA A10G` | confirmed | NVIDIA A10G | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA A10G' = NVIDIA A10G (official nvidia-smi / Kubernetes product name). Paired with 2237:10de x3. |
| `NVIDIA B200` | confirmed | NVIDIA B200 | official nvidia-smi / Kubernetes product name | 2 | 'NVIDIA B200' = NVIDIA B200 (official nvidia-smi / Kubernetes product name). Paired with 2901:10de x2. |
| `NVIDIA H100 80GB HBM3` | confirmed | NVIDIA H100 SXM5 80GB | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA H100 80GB HBM3' = NVIDIA H100 SXM5 80GB (official nvidia-smi / Kubernetes product name). Paired with 2330:10de x3. |
| `NVIDIA H100 NVL` | confirmed | NVIDIA H100 NVL | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA H100 NVL' = NVIDIA H100 NVL (official nvidia-smi / Kubernetes product name). Paired with 2321:10de x3. |
| `NVIDIA L4` | confirmed | NVIDIA L4 | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA L4' = NVIDIA L4 (official nvidia-smi / Kubernetes product name). Paired with 27b8:10de x3. |
| `NVIDIA L40S` | confirmed | NVIDIA L40S | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA L40S' = NVIDIA L40S (official nvidia-smi / Kubernetes product name). Paired with 26b9:10de x3. |
| `NVIDIA-A100-SXM4-40GB` | confirmed | NVIDIA A100-SXM4-40GB | official nvidia-smi / Kubernetes product name | 5 | 'NVIDIA-A100-SXM4-40GB' = NVIDIA A100-SXM4-40GB (official nvidia-smi / Kubernetes product name). Paired with 20b0:10de x5. |
| `NVIDIA-A100-SXM4-80GB` | confirmed | NVIDIA A100-SXM4-80GB | official nvidia-smi / Kubernetes product name | 31 | 'NVIDIA-A100-SXM4-80GB' = NVIDIA A100-SXM4-80GB (official nvidia-smi / Kubernetes product name). Paired with 20b2:10de x30. |
| `NVIDIA-A10G` | confirmed | NVIDIA A10G | official nvidia-smi / Kubernetes product name | 3 | 'NVIDIA-A10G' = NVIDIA A10G (official nvidia-smi / Kubernetes product name). Paired with 2237:10de x3. |
| `NVIDIA-B200` | confirmed | NVIDIA B200 | official nvidia-smi / Kubernetes product name | 46 | 'NVIDIA-B200' = NVIDIA B200 (official nvidia-smi / Kubernetes product name). Paired with 2901:10de x38. |
| `NVIDIA-B300-SXM6-AC` | confirmed | NVIDIA B300 SXM6 AC | official nvidia-smi / Kubernetes product name | 21 | 'NVIDIA-B300-SXM6-AC' = NVIDIA B300 SXM6 AC (official nvidia-smi / Kubernetes product name). Paired with 3182:10de x12. |
| `NVIDIA-GB10` | confirmed | NVIDIA GB10 (DGX Spark) | official nvidia-smi / Kubernetes product name | 21 | 'NVIDIA-GB10' = NVIDIA GB10 (DGX Spark) (official nvidia-smi / Kubernetes product name). Paired with 2e12:10de x20. |
| `NVIDIA-GB200` | confirmed | NVIDIA GB200 | official nvidia-smi / Kubernetes product name | 17 | 'NVIDIA-GB200' = NVIDIA GB200 (official nvidia-smi / Kubernetes product name). Paired with 2941:10de x11. |
| `NVIDIA-GB300` | confirmed | NVIDIA GB300, NVIDIA GB300 Desktop (DGX Station) | official nvidia-smi / Kubernetes product name | 21 | 'NVIDIA-GB300' = NVIDIA GB300, NVIDIA GB300 Desktop (DGX Station) (official nvidia-smi / Kubernetes product name). Paired with 31c2:10de x15. |
| `NVIDIA-GB300-WS` | inferred | NVIDIA GB300 Desktop (DGX Station) | manifest description | 1 | 'NVIDIA-GB300-WS' = NVIDIA GB300 Desktop (DGX Station) (manifest description). |
| `NVIDIA-GeForce-RTX-4090` | confirmed | NVIDIA GeForce RTX 4090 | official nvidia-smi / Kubernetes product name | 2 | 'NVIDIA-GeForce-RTX-4090' = NVIDIA GeForce RTX 4090 (official nvidia-smi / Kubernetes product name). Paired with 2684:10de x2. |
| `NVIDIA-GH200-144G-HBM3e` | confirmed | NVIDIA GH200 144G HBM3e | official nvidia-smi / Kubernetes product name | 5 | 'NVIDIA-GH200-144G-HBM3e' = NVIDIA GH200 144G HBM3e (official nvidia-smi / Kubernetes product name). Paired with 2348:10de x5. |
| `NVIDIA-GH200-480GB` | confirmed | NVIDIA GH200 480GB / 120GB | official nvidia-smi / Kubernetes product name | 5 | 'NVIDIA-GH200-480GB' = NVIDIA GH200 480GB / 120GB (official nvidia-smi / Kubernetes product name). Paired with 2342:10de x5. |
| `NVIDIA-H100-80GB-HBM3` | confirmed | NVIDIA H100 SXM5 80GB | official nvidia-smi / Kubernetes product name | 36 | 'NVIDIA-H100-80GB-HBM3' = NVIDIA H100 SXM5 80GB (official nvidia-smi / Kubernetes product name). Paired with 2330:10de x31. |
| `NVIDIA-H100-NVL` | confirmed | NVIDIA H100 NVL | official nvidia-smi / Kubernetes product name | 12 | 'NVIDIA-H100-NVL' = NVIDIA H100 NVL (official nvidia-smi / Kubernetes product name). Paired with 2321:10de x10. |
| `NVIDIA-H200` | confirmed | NVIDIA H200 SXM 141GB | official nvidia-smi / Kubernetes product name | 45 | 'NVIDIA-H200' = NVIDIA H200 SXM 141GB (official nvidia-smi / Kubernetes product name). Paired with 2335:10de x38. |
| `NVIDIA-H200-NVL` | confirmed | NVIDIA H200 NVL | official nvidia-smi / Kubernetes product name | 9 | 'NVIDIA-H200-NVL' = NVIDIA H200 NVL (official nvidia-smi / Kubernetes product name). Paired with 233b:10de x9. |
| `NVIDIA-L40S` | confirmed | NVIDIA L40S | official nvidia-smi / Kubernetes product name | 23 | 'NVIDIA-L40S' = NVIDIA L40S (official nvidia-smi / Kubernetes product name). Paired with 26b9:10de x23. |
| `NVIDIA-RTX-6000-Ada-Generation` | confirmed | NVIDIA RTX 6000 Ada Generation | official nvidia-smi / Kubernetes product name | 2 | 'NVIDIA-RTX-6000-Ada-Generation' = NVIDIA RTX 6000 Ada Generation (official nvidia-smi / Kubernetes product name). Paired with 26b1:10de x2. |
| `NVIDIA-RTX-PRO-4500-Blackwell-Server-Edition` | confirmed | NVIDIA RTX PRO 4500 Blackwell Server Edition | official nvidia-smi / Kubernetes product name | 8 | 'NVIDIA-RTX-PRO-4500-Blackwell-Server-Edition' = NVIDIA RTX PRO 4500 Blackwell Server Edition (official nvidia-smi / Kubernetes product name). Paired with 2c3a:10de x8. |
| `NVIDIA-RTX-PRO-6000-Blackwell-Server-Edition` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | official nvidia-smi / Kubernetes product name | 11 | 'NVIDIA-RTX-PRO-6000-Blackwell-Server-Edition' = NVIDIA RTX PRO 6000 Blackwell Server Edition (official nvidia-smi / Kubernetes product name). Paired with 2bb5:10de x10. |
| `NVIDIA-RTX-PRO-6000-Blackwell-Workstation-Edition` | confirmed | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | official nvidia-smi / Kubernetes product name | 1 | 'NVIDIA-RTX-PRO-6000-Blackwell-Workstation-Edition' = NVIDIA RTX PRO 6000 Blackwell Workstation Edition (official nvidia-smi / Kubernetes product name). Paired with 2bb1:10de x1. |
| `RTX PRO 6000 Blackwell Server Edition` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | official nvidia-smi / Kubernetes product name | 2 | 'RTX PRO 6000 Blackwell Server Edition' = NVIDIA RTX PRO 6000 Blackwell Server Edition (official nvidia-smi / Kubernetes product name). No gpu_device: engines selected by compute capability 12.0. |
| `rtx-pro-4500-blackwell` | confirmed | NVIDIA RTX PRO 4500 Blackwell | official nvidia-smi / Kubernetes product name | 2 | 'rtx-pro-4500-blackwell' = NVIDIA RTX PRO 4500 Blackwell (official nvidia-smi / Kubernetes product name). Paired with 2c31:10de x2. |
| `rtx-pro-6k-bse` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | gpu_device co-occurrence | 12 | 'rtx-pro-6k-bse' = NVIDIA RTX PRO 6000 Blackwell Server Edition (gpu_device co-occurrence). Paired with 2bb5:10de x12. |
| `rtx-pro-6k-bwe` | confirmed | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | gpu_device co-occurrence | 10 | 'rtx-pro-6k-bwe' = NVIDIA RTX PRO 6000 Blackwell Workstation Edition (gpu_device co-occurrence). Paired with 2bb1:10de x10. |
| `rtx-pro-6k-bwe-maxq` | confirmed | NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition | gpu_device co-occurrence | 6 | 'rtx-pro-6k-bwe-maxq' = NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition (gpu_device co-occurrence). Paired with 2bb4:10de x6. |
| `RTX3070` | confirmed | NVIDIA GeForce RTX 3070 | alias | 1 | 'RTX3070' = NVIDIA GeForce RTX 3070 (alias). No gpu_device: engines selected by compute capability 8.6. |
| `RTX3080` | confirmed | NVIDIA GeForce RTX 3080 | alias | 1 | 'RTX3080' = NVIDIA GeForce RTX 3080 (alias). No gpu_device: engines selected by compute capability 8.6. |
| `RTX4080` | confirmed | NVIDIA GeForce RTX 4080 | alias | 2 | 'RTX4080' = NVIDIA GeForce RTX 4080 (alias). Paired with 2704:10de x2. |
| `RTX4090` | confirmed | NVIDIA GeForce RTX 4090 | alias | 9 | 'RTX4090' = NVIDIA GeForce RTX 4090 (alias). Paired with 2684:10de x6. |
| `rtx4090` | confirmed | NVIDIA GeForce RTX 4090 | alias | 29 | 'rtx4090' = NVIDIA GeForce RTX 4090 (alias). Paired with 2684:10de x27. |
| `rtx4500` | confirmed | NVIDIA RTX 4500 Ada Generation | gpu_device co-occurrence | 2 | 'rtx4500' = NVIDIA RTX 4500 Ada Generation (gpu_device co-occurrence). Paired with 27b1:10de x2. |
| `RTX5080` | confirmed | NVIDIA GeForce RTX 5080 | alias | 9 | 'RTX5080' = NVIDIA GeForce RTX 5080 (alias). Paired with 2c02:10de x2. Invalid device ids: 2e01:10de x4. |
| `rtx5080` | confirmed | NVIDIA GeForce RTX 5080 | alias | 22 | 'rtx5080' = NVIDIA GeForce RTX 5080 (alias). Paired with 2c02:10de x20. |
| `RTX5090` | confirmed | NVIDIA GeForce RTX 5090 | alias | 15 | 'RTX5090' = NVIDIA GeForce RTX 5090 (alias). Paired with 2b85:10de x2. Invalid device ids: 2e02:10de x4. |
| `rtx5090` | confirmed | NVIDIA GeForce RTX 5090 | alias | 22 | 'rtx5090' = NVIDIA GeForce RTX 5090 (alias). Paired with 2b85:10de x20. |
| `RTX6000` | confirmed | NVIDIA RTX 6000 Ada Generation | gpu_device co-occurrence | 4 | 'RTX6000' = NVIDIA RTX 6000 Ada Generation (gpu_device co-occurrence). Paired with 26b1:10de x4. |
| `RTX6000-ADA` | confirmed | NVIDIA RTX 6000 Ada Generation | alias | 2 | 'RTX6000-ADA' = NVIDIA RTX 6000 Ada Generation (alias). Paired with 26b1:10de x2. |
| `rtx6000-ada` | confirmed | NVIDIA RTX 6000 Ada Generation | alias | 23 | 'rtx6000-ada' = NVIDIA RTX 6000 Ada Generation (alias). Paired with 26b1:10de x21. |
| `rtx6000-blackwell-sv` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | gpu_device co-occurrence | 28 | 'rtx6000-blackwell-sv' = NVIDIA RTX PRO 6000 Blackwell Server Edition (gpu_device co-occurrence). Paired with 2bb5:10de x24. Invalid device ids: 2e14:10de x4. |
| `rtx6000-blackwell-ws` | confirmed | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | gpu_device co-occurrence | 24 | 'rtx6000-blackwell-ws' = NVIDIA RTX PRO 6000 Blackwell Workstation Edition (gpu_device co-occurrence). Paired with 2bb1:10de x24. |
| `rtx6000_ada` | confirmed | NVIDIA RTX 6000 Ada Generation | alias | 6 | 'rtx6000_ada' = NVIDIA RTX 6000 Ada Generation (alias). Paired with 26b1:10de x6. |
| `rtx6000_blackwell_sv` | confirmed | NVIDIA RTX PRO 6000 Blackwell Server Edition | gpu_device co-occurrence | 30 | 'rtx6000_blackwell_sv' = NVIDIA RTX PRO 6000 Blackwell Server Edition (gpu_device co-occurrence). Paired with 2bb5:10de x30. |
| `rtx6000_blackwell_ws` | confirmed | NVIDIA RTX PRO 6000 Blackwell Workstation Edition | gpu_device co-occurrence | 4 | 'rtx6000_blackwell_ws' = NVIDIA RTX PRO 6000 Blackwell Workstation Edition (gpu_device co-occurrence). Paired with 2bb1:10de x4. |
| `T4` | confirmed | NVIDIA Tesla T4 | alias | 8 | 'T4' = NVIDIA Tesla T4 (alias). No gpu_device: engines selected by compute capability 7.5. |
| `V100` | confirmed | NVIDIA Tesla V100 | alias | 5 | 'V100' = NVIDIA Tesla V100 (alias). No gpu_device: engines selected by compute capability 7.0. |
