#!/usr/bin/env python3
"""Tests for resource_monitor.py.

About me: plain assert-based checks (no pytest, matching the other
stack-level test scripts). Host-runnable - uses temp files/dirs standing
in for /proc/meminfo and /sys/class/drm/card*/device/, no real system
introspection needed: `python3 test_resource_monitor.py`
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import resource_monitor as rm  # noqa: E402

failures = []


def check(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}")
    if not condition:
        failures.append(name)


TMP = tempfile.mkdtemp(prefix="resource-monitor-test-")

# --------------------------------------------------------------------------
# read_cpu_percent
# --------------------------------------------------------------------------

check("read_cpu_percent normalizes load average by core count",
      rm.read_cpu_percent(loadavg_fn=lambda: (4.0, 3.0, 2.0),
                          cpu_count_fn=lambda: 8) == 50.0)
check("read_cpu_percent caps at 999.0 for an absurdly high load average",
      rm.read_cpu_percent(loadavg_fn=lambda: (500.0, 1.0, 1.0),
                          cpu_count_fn=lambda: 1) == 999.0)
check("read_cpu_percent falls back to 1 core if cpu_count is None",
      rm.read_cpu_percent(loadavg_fn=lambda: (2.0, 1.0, 1.0),
                          cpu_count_fn=lambda: None) == 200.0)


def raising_loadavg():
    raise OSError("not supported on this platform")


check("read_cpu_percent returns None when unavailable (e.g. non-Unix host)",
      rm.read_cpu_percent(loadavg_fn=raising_loadavg) is None)

# --------------------------------------------------------------------------
# read_ram_usage
# --------------------------------------------------------------------------

meminfo_path = os.path.join(TMP, "meminfo")
with open(meminfo_path, "w", encoding="utf-8") as f:
    f.write("MemTotal:       31695584 kB\n"
            "MemFree:         2597540 kB\n"
            "MemAvailable:    6799220 kB\n"
            "Buffers:          123456 kB\n")

ram = rm.read_ram_usage(meminfo_path)
check("read_ram_usage parses MemTotal/MemAvailable into GB",
      ram is not None and abs(ram["total_gb"] - 31695584 / 1024 / 1024) < 1e-6)
check("read_ram_usage computes used = total - available",
      abs(ram["used_gb"] - (31695584 - 6799220) / 1024 / 1024) < 1e-6)
check("read_ram_usage computes a sane percent",
      0 < ram["percent"] < 100)

no_available_path = os.path.join(TMP, "meminfo_no_available")
with open(no_available_path, "w", encoding="utf-8") as f:
    f.write("MemTotal:       1000 kB\n")

ram_no_avail = rm.read_ram_usage(no_available_path)
check("read_ram_usage treats a missing MemAvailable as 0 available "
      "(100% used) rather than crashing",
      ram_no_avail is not None and ram_no_avail["percent"] == 100.0)

check("read_ram_usage returns None when the file doesn't exist",
      rm.read_ram_usage(os.path.join(TMP, "does-not-exist")) is None)

garbage_path = os.path.join(TMP, "meminfo_garbage")
with open(garbage_path, "w", encoding="utf-8") as f:
    f.write("MemTotal:       not-a-number kB\n")
check("read_ram_usage returns None on unparseable content",
      rm.read_ram_usage(garbage_path) is None)

# --------------------------------------------------------------------------
# find_gpu_card / read_gpu_usage
# --------------------------------------------------------------------------

drm_root = os.path.join(TMP, "drm")
small_card = os.path.join(drm_root, "card0", "device")
big_card = os.path.join(drm_root, "card1", "device")
os.makedirs(small_card)
os.makedirs(big_card)

with open(os.path.join(small_card, "mem_info_vram_total"), "w") as f:
    f.write("536870912\n")  # 512MB - integrated/virtual
with open(os.path.join(small_card, "mem_info_vram_used"), "w") as f:
    f.write("16773120\n")

with open(os.path.join(big_card, "mem_info_vram_total"), "w") as f:
    f.write("17095983104\n")  # ~16GB - the real dedicated GPU
with open(os.path.join(big_card, "mem_info_vram_used"), "w") as f:
    f.write("5015105536\n")

glob_pattern = os.path.join(drm_root, "card*", "device")
check("find_gpu_card picks the card with the most VRAM (the real dedicated "
      "GPU, not an integrated/virtual one)",
      rm.find_gpu_card(glob_pattern) == big_card)

gpu = rm.read_gpu_usage(glob_pattern)
check("read_gpu_usage reports usage for the picked (biggest) card",
      gpu is not None and abs(gpu["total_gb"] - 17095983104 / 1024**3) < 1e-6 and
      abs(gpu["used_gb"] - 5015105536 / 1024**3) < 1e-6)
check("read_gpu_usage computes a sane percent",
      0 < gpu["percent"] < 100)

empty_glob = os.path.join(TMP, "no-such-drm", "card*", "device")
check("find_gpu_card returns None when there is no GPU at all",
      rm.find_gpu_card(empty_glob) is None)
check("read_gpu_usage returns None when there is no GPU at all",
      rm.read_gpu_usage(empty_glob) is None)

# --------------------------------------------------------------------------

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")
