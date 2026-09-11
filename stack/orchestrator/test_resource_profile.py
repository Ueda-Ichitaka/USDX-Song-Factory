#!/usr/bin/env python3
"""Tests for resource_profile.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Pure functions, stdlib only - host-runnable:
`python3 test_resource_profile.py`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resource_profile as rp  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
# select_whisper_model: RAM-driven (whisper always runs on CPU in this stack)
# --------------------------------------------------------------------------

check("no RAM given -> None (caller keeps its own default)",
      rp.select_whisper_model(None)[0] is None)

model, _ = rp.select_whisper_model(8.0)
check(f"8 GB RAM -> large-v2 (got {model})", model == "large-v2")

model, _ = rp.select_whisper_model(4.0)
check(f"4 GB RAM -> medium (got {model})", model == "medium")

model, _ = rp.select_whisper_model(2.0)
check(f"2 GB RAM -> small (got {model})", model == "small")

model, _ = rp.select_whisper_model(1.0)
check(f"1 GB RAM -> base (got {model})", model == "base")

model, _ = rp.select_whisper_model(0.5)
check(f"0.5 GB RAM -> tiny (got {model})", model == "tiny")

# swap is a small, CAPPED cushion only - a huge swap pool (zram is often
# 100s of GB) must never be credited as if it were that much real RAM
model, _ = rp.select_whisper_model(5.5, swap_gb=100.0)
check(f"a small amount of swap can nudge the effective RAM a little "
      f"(got {model})", model == "large-v2")  # 5.5 + min(100,8)*0.15=6.7
model_huge_swap, _ = rp.select_whisper_model(1.0, swap_gb=100000.0)
check(f"an enormous swap pool does not get credited as real capacity "
      f"(got {model_huge_swap})",
      model_huge_swap == "small")  # 1.0 + min(100000,8)*0.15=2.2, still <3.0

model2, _ = rp.select_whisper_model(1.0, swap_gb=0.0)
check("swap=0 behaves the same as swap=None",
      model2 == rp.select_whisper_model(1.0)[0])

# CPU core count can cap (never raise) the RAM-driven choice
model, reason = rp.select_whisper_model(8.0, cpu_cores=1)
check(f"1 CPU core caps an 8GB choice down to tiny (got {model})",
      model == "tiny" and "core" in reason)

model, reason = rp.select_whisper_model(8.0, cpu_cores=3)
check(f"3 CPU cores cap an 8GB choice down to medium (got {model})",
      model == "medium" and "core" in reason)

model, reason = rp.select_whisper_model(8.0, cpu_cores=16)
check(f"plenty of cores does not cap anything (got {model})",
      model == "large-v2" and "core" not in reason)

# --------------------------------------------------------------------------
# select_whisper_batch_size: CPU-core-driven
# --------------------------------------------------------------------------

check("no cpu_cores given -> None",
      rp.select_whisper_batch_size(None)[0] is None)
check("4 cores -> batch size 4", rp.select_whisper_batch_size(4)[0] == 4)
check("32 cores -> capped at 16", rp.select_whisper_batch_size(32)[0] == 16)
check("0 cores -> floored at 1", rp.select_whisper_batch_size(0)[0] == 1)

# --------------------------------------------------------------------------
# select_demucs_model: VRAM-driven on GPU, RAM+cores-driven on CPU
# --------------------------------------------------------------------------

check("cuda device, no VRAM given -> None",
      rp.select_demucs_model("cuda")[0] is None)

model, _ = rp.select_demucs_model("cuda", vram_gb=16.0)
check(f"16 GB VRAM -> htdemucs_ft (got {model})", model == rp.DEMUCS_MODEL_BEST)

model, _ = rp.select_demucs_model("cuda", vram_gb=4.0)
check(f"4 GB VRAM -> htdemucs (got {model})", model == rp.DEMUCS_MODEL_DEFAULT)

model, _ = rp.select_demucs_model("cuda", vram_gb=1.0)
check(f"1 GB VRAM -> mdx_extra_q (got {model})", model == rp.DEMUCS_MODEL_LIGHT)

check("cpu device, no RAM given -> None",
      rp.select_demucs_model("cpu")[0] is None)

model, _ = rp.select_demucs_model("cpu", ram_gb=12.0, cpu_cores=8)
check(f"12 GB RAM + 8 cores (cpu) -> htdemucs_ft (got {model})",
      model == rp.DEMUCS_MODEL_BEST)

model, _ = rp.select_demucs_model("cpu", ram_gb=12.0, cpu_cores=2)
check(f"12 GB RAM but only 2 cores (cpu) -> htdemucs, not _ft (got {model})",
      model == rp.DEMUCS_MODEL_DEFAULT)

model, _ = rp.select_demucs_model("cpu", ram_gb=6.0)
check(f"6 GB RAM (cpu) -> htdemucs (got {model})", model == rp.DEMUCS_MODEL_DEFAULT)

model, _ = rp.select_demucs_model("cpu", ram_gb=2.0)
check(f"2 GB RAM (cpu) -> mdx_extra_q (got {model})", model == rp.DEMUCS_MODEL_LIGHT)

# matches this project's actual configured hardware (05-LESSONS.md): ~8GB
# RAM budget for the stack, 16 CPU threads, RX 9070 = 16GB VRAM
model, _ = rp.select_whisper_model(8.0, swap_gb=250.0, cpu_cores=16)
check(f"this project's real host budget -> large-v2 (got {model})",
      model == "large-v2")
model, _ = rp.select_demucs_model("cuda", vram_gb=16.0)
check(f"this project's real GPU -> htdemucs_ft (got {model})",
      model == rp.DEMUCS_MODEL_BEST)
# the CPU service's demucs choice must stay htdemucs (matching the
# pre-auto-selection default) - the 250GB zram pool must NOT inflate the
# effective RAM enough to silently switch it to the 4x-slower htdemucs_ft
model, _ = rp.select_demucs_model("cpu", ram_gb=8.0, swap_gb=250.0, cpu_cores=16)
check(f"this project's real CPU-service budget -> htdemucs, not _ft (got {model})",
      model == rp.DEMUCS_MODEL_DEFAULT)

# --------------------------------------------------------------------------
# describe_profile: human-readable summary, never crashes on all-None input
# --------------------------------------------------------------------------

text = rp.describe_profile(None, None, None, None, "cpu")
check("describe_profile handles all-None input without crashing",
      "(default)" in text)

text2 = rp.describe_profile(8.0, 250.0, 16, 16.0, "cuda")
check("describe_profile includes the resolved whisper model",
      "large-v2" in text2)
check("describe_profile includes the resolved demucs model",
      rp.DEMUCS_MODEL_BEST in text2)

# --------------------------------------------------------------------------
# apply_safety_margin: a declared budget (esp. VRAM shared with a desktop
# - multiple monitors, browser, compositor, ...) must never be used at
# face value - a margin is subtracted before it feeds model selection
# --------------------------------------------------------------------------

check("apply_safety_margin subtracts the margin",
      rp.apply_safety_margin(16.0, 2.0) == 14.0)
check("apply_safety_margin floors at 0 (never goes negative)",
      rp.apply_safety_margin(1.0, 5.0) == 0.0)
check("apply_safety_margin passes through None unchanged (nothing declared)",
      rp.apply_safety_margin(None, 2.0) is None)
check("apply_safety_margin treats a missing margin as 0",
      rp.apply_safety_margin(16.0, None) == 16.0)

# concrete case from the user's real machine: 16 GB VRAM shared with 3
# monitors + desktop apps must not be treated as 16 GB available to demucs
effective_vram = rp.apply_safety_margin(16.0, 2.0)
model, reason = rp.select_demucs_model("cuda", vram_gb=effective_vram)
check(f"16 GB VRAM with a 2 GB margin still affords htdemucs_ft "
      f"(got {model}, {reason})", model == rp.DEMUCS_MODEL_BEST)

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
