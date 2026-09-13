#!/usr/bin/env python3
"""Live system resource readings for the progress dashboard (see
orchestrator.py:render_progress()/cmd_progress()).

About me: stdlib + sysfs only, no new dependency (psutil, rocm-smi,
nvidia-smi are not installed in the image - see stack/Dockerfile and
knowledge/02-DESIGN.md). CPU/RAM come from stdlib (os.getloadavg(),
/proc/meminfo); GPU VRAM comes straight from the AMD amdgpu kernel
driver's own sysfs files (mem_info_vram_used/_total under
/sys/class/drm/card*/device/) - confirmed live against a real RX 9070,
no extra tooling needed. Every function is best-effort: returns None
(never raises) when the expected file/entry isn't there - e.g. a non-
Linux host, or no GPU present - since this is a monitoring nicety, not
something any job's success should ever depend on.
"""

import glob
import os


def read_cpu_percent(loadavg_fn=os.getloadavg, cpu_count_fn=os.cpu_count):
    """Approximate CPU utilization as the 1-minute load average
    normalized by core count. A genuine instantaneous "CPU %" needs two
    /proc/stat samples some time apart - load average is a good enough
    single-shot proxy for a periodically-refreshed dashboard, and it's
    the same number `uptime`/`top` already show. None if unavailable."""
    try:
        load1 = loadavg_fn()[0]
        cores = cpu_count_fn() or 1
        return min(load1 / cores * 100.0, 999.0)
    except OSError:
        return None


def read_ram_usage(meminfo_path="/proc/meminfo"):
    """{"used_gb", "total_gb", "percent"} parsed from /proc/meminfo, or
    None if it can't be read/parsed (e.g. not on Linux)."""
    try:
        values = {}
        with open(meminfo_path, encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(rest.strip().split()[0])  # kB
    except (OSError, ValueError):
        return None
    total_kb = values.get("MemTotal")
    if not total_kb:
        return None
    avail_kb = values.get("MemAvailable", 0)
    used_kb = total_kb - avail_kb
    return {
        "total_gb": total_kb / 1024 / 1024,
        "used_gb": used_kb / 1024 / 1024,
        "percent": used_kb / total_kb * 100.0,
    }


def find_gpu_card(drm_glob_pattern="/sys/class/drm/card*/device"):
    """The device dir of the GPU with the most total VRAM - a real
    dedicated GPU almost always dwarfs an integrated/virtual one on the
    same host, so this picks the right card without needing rocm-smi/
    nvidia-smi (confirmed live: correctly picked a real 16GB RX 9070
    over a 512MB onboard/virtual device). None if no card on this host
    exposes VRAM info at all (no GPU, or a non-amdgpu driver)."""
    best_dir, best_total = None, -1
    for device_dir in glob.glob(drm_glob_pattern):
        try:
            with open(os.path.join(device_dir, "mem_info_vram_total"),
                     encoding="utf-8") as f:
                total = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if total > best_total:
            best_dir, best_total = device_dir, total
    return best_dir


def read_gpu_usage(drm_glob_pattern="/sys/class/drm/card*/device"):
    """{"used_gb", "total_gb", "percent"} for the GPU found by
    find_gpu_card(), or None (no GPU / not readable)."""
    card_dir = find_gpu_card(drm_glob_pattern)
    if card_dir is None:
        return None
    try:
        with open(os.path.join(card_dir, "mem_info_vram_total"), encoding="utf-8") as f:
            total = int(f.read().strip())
        with open(os.path.join(card_dir, "mem_info_vram_used"), encoding="utf-8") as f:
            used = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if not total:
        return None
    gib = 1024 ** 3
    return {
        "total_gb": total / gib, "used_gb": used / gib,
        "percent": used / total * 100.0,
    }
