#!/usr/bin/env python3
"""Auto-select UltraSinger's model/quality settings from a declared
resource budget (RAM/swap/CPU cores/VRAM).

About me: purely advisory, pure functions, no I/O - orchestrator.py reads
the STACK_RAM_GB/STACK_SWAP_GB/STACK_CPU_CORES/STACK_VRAM_GB env vars and
calls these; every setting picked here can still be overridden directly
(WHISPER_MODEL/DEMUCS_MODEL/WHISPER_BATCH_SIZE always wins when set).

Whisper always runs on CPU in this stack (CTranslate2 has no ROCm support,
see 02-DESIGN.md "Device strategy") - so whisper model size is sized from
RAM + CPU cores, never VRAM. Demucs separation runs on the GPU on the ROCm
service and on CPU otherwise, so it is sized from VRAM (device=cuda) or
RAM+CPU cores (device=cpu).

Thresholds are heuristic - a mix of commonly-cited faster-whisper
VRAM footprints (roughly halved for CPU+int8, which is what this stack
always uses for whisper) and this project's own measured peak (~4.3 GB
for large-v2 int8 + demucs together on an 8 GB budget, see 05-LESSONS.md
"Host / environment") - not a benchmarked guarantee for your hardware.
Swap is treated only as a small safety cushion against transient spikes,
never as capacity that justifies a bigger model - relying on swap for
multi-GB model weights would be a performance disaster.
"""

WHISPER_MODELS_BY_SIZE = ["tiny", "base", "small", "medium", "large-v2"]
DEMUCS_MODEL_DEFAULT = "htdemucs"
DEMUCS_MODEL_LIGHT = "mdx_extra_q"
DEMUCS_MODEL_BEST = "htdemucs_ft"


def apply_safety_margin(value_gb, margin_gb):
    """Subtract a safety margin from a declared resource budget, floored
    at 0 - a declared RAM/VRAM figure is rarely ALL available to this
    stack alone (the OS, a desktop compositor, other apps, other GPU
    consumers like extra monitors all take a share too). None (nothing
    declared) passes through unchanged; a missing margin is treated as 0.
    """
    if value_gb is None:
        return None
    return max(0.0, value_gb - (margin_gb or 0.0))


def _effective_ram_gb(ram_gb, swap_gb):
    if ram_gb is None:
        return None
    if swap_gb:
        # a modest, CAPPED cushion for transient spikes only - never sized
        # as if it were real, fast memory. Capped so a large swap pool
        # (zram, 100s of GB is common) can't inflate the effective budget
        # into picking a much bigger/slower model than the real RAM supports.
        return ram_gb + min(swap_gb, 8.0) * 0.15
    return ram_gb


def select_whisper_model(ram_gb, swap_gb=None, cpu_cores=None):
    """Returns (model_name, reasoning) or (None, reasoning) when there is
    nothing to go on (caller should keep its own default)."""
    eff_ram = _effective_ram_gb(ram_gb, swap_gb)
    if eff_ram is None:
        return None, "no RAM budget given (STACK_RAM_GB unset) - keeping default"

    if eff_ram >= 6.0:
        model = "large-v2"
    elif eff_ram >= 3.0:
        model = "medium"
    elif eff_ram >= 1.5:
        model = "small"
    elif eff_ram >= 0.8:
        model = "base"
    else:
        model = "tiny"
    reason = f"{eff_ram:.1f} GB effective RAM"

    # a big model on CPU with very few cores can take a very long time per
    # song - step down when cores are the binding constraint
    if cpu_cores is not None:
        idx = WHISPER_MODELS_BY_SIZE.index(model)
        if cpu_cores < 2:
            idx = min(idx, WHISPER_MODELS_BY_SIZE.index("tiny"))
        elif cpu_cores < 4:
            idx = min(idx, WHISPER_MODELS_BY_SIZE.index("medium"))
        if WHISPER_MODELS_BY_SIZE[idx] != model:
            reason += f", capped by only {cpu_cores} CPU core(s)"
            model = WHISPER_MODELS_BY_SIZE[idx]

    return model, reason


def select_whisper_batch_size(cpu_cores=None):
    """Whisper batch size mainly trades CPU/RAM usage during inference for
    speed - scale it with available cores. Returns (batch_size, reasoning)
    or (None, reasoning) when there is nothing to go on."""
    if cpu_cores is None:
        return None, "no CPU core count given (STACK_CPU_CORES unset) - keeping default"
    batch_size = max(1, min(16, cpu_cores))
    return batch_size, f"{cpu_cores} CPU core(s)"


def select_demucs_model(device, vram_gb=None, ram_gb=None, swap_gb=None, cpu_cores=None):
    """Returns (model_name, reasoning) or (None, reasoning)."""
    if device == "cuda":
        if vram_gb is None:
            return None, "no VRAM budget given (STACK_VRAM_GB unset) - keeping default"
        if vram_gb >= 6.0:
            return DEMUCS_MODEL_BEST, f"{vram_gb:.1f} GB VRAM (GPU, best-quality model affordable)"
        if vram_gb >= 2.0:
            return DEMUCS_MODEL_DEFAULT, f"{vram_gb:.1f} GB VRAM"
        return DEMUCS_MODEL_LIGHT, f"only {vram_gb:.1f} GB VRAM"

    eff_ram = _effective_ram_gb(ram_gb, swap_gb)
    if eff_ram is None:
        return None, "no RAM budget given (STACK_RAM_GB unset) - keeping default"
    if eff_ram >= 10.0 and (cpu_cores is None or cpu_cores >= 8):
        return DEMUCS_MODEL_BEST, f"{eff_ram:.1f} GB effective RAM, {cpu_cores or '?'} CPU core(s)"
    if eff_ram >= 4.0:
        return DEMUCS_MODEL_DEFAULT, f"{eff_ram:.1f} GB effective RAM"
    return DEMUCS_MODEL_LIGHT, f"only {eff_ram:.1f} GB effective RAM"


def describe_profile(ram_gb, swap_gb, cpu_cores, vram_gb, device):
    """One human-readable summary line per auto-selected setting, for the
    orchestrator to print at startup so the choice is never a black box."""
    whisper_model, whisper_reason = select_whisper_model(ram_gb, swap_gb, cpu_cores)
    batch_size, batch_reason = select_whisper_batch_size(cpu_cores)
    demucs_model, demucs_reason = select_demucs_model(device, vram_gb, ram_gb, swap_gb, cpu_cores)

    lines = ["Resource-based auto-selection "
            f"(RAM={ram_gb}GB swap={swap_gb}GB cpu={cpu_cores} vram={vram_gb}GB device={device}):"]
    lines.append(f"  whisper model: {whisper_model or '(default)'} - {whisper_reason}")
    lines.append(f"  whisper batch size: {batch_size or '(default)'} - {batch_reason}")
    lines.append(f"  demucs model: {demucs_model or '(default)'} - {demucs_reason}")
    return "\n".join(lines)
