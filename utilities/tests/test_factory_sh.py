#!/usr/bin/env python3
"""Tests for factory.sh (repository root).

About me: plain assert-based checks (no pytest), host only. docker is a
fake on PATH that records its arguments (and plays running containers),
/proc/meminfo a fake file, the stack input and stack/.env temp files - no
container is started and the real stack/input is never touched. Run (from
utilities/): python3 tests/test_factory_sh.py
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(REPO, "factory.sh")

failures = []


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        failures.append(name)


root = tempfile.mkdtemp()
bin_dir = os.path.join(root, "bin")
os.makedirs(bin_dir)
calls = os.path.join(root, "docker-calls")
with open(os.path.join(bin_dir, "docker"), "w") as f:
    f.write("""#!/usr/bin/env bash
echo "$*" >> "$FAKE_DOCKER_LOG"
pwd > "$FAKE_DOCKER_LOG.pwd"
case "$*" in
  *"ps --status running -q"*) [ -n "${FAKE_STACK_RUNNING:-}" ] && echo abc123 ;;
  "ps --filter name=^library-check$ -q") [ -n "${FAKE_CHECK_RUNNING:-}" ] && echo def456 ;;
esac
exit "${FAKE_DOCKER_EXIT:-0}"
""")
os.chmod(os.path.join(bin_dir, "docker"), 0o755)

meminfo = os.path.join(root, "meminfo")
stack_input = os.path.join(root, "input")
os.makedirs(stack_input)
env_file = os.path.join(root, ".env")
lib = os.path.join(root, "lib")
os.makedirs(os.path.join(lib, "ABBA - Chiquitita"))
with open(os.path.join(lib, "ABBA - Chiquitita", "ABBA - Chiquitita.txt"), "w", encoding="utf-8") as f:
    f.write("#TITLE:Chiquitita\n#ARTIST:ABBA\n#GENRE:Pop\n: 0 4 0 la\nE\n")
with open(os.path.join(lib, "ABBA - Chiquitita", "a.mp3"), "w") as f:
    f.write("audio")
with open(os.path.join(stack_input, "broken.csv"), "w", encoding="utf-8") as f:
    f.write("band,song name,category,description\nABBA,Chiquitita,gap,\n")
with open(os.path.join(stack_input, "genre-edits.tsv"), "w", encoding="utf-8") as f:
    f.write("ABBA - Chiquitita/ABBA - Chiquitita.txt\tPop, Disco\nABBA - Chiquitita/language.txt\tPop\n")


def run(*args, free_gb=20, env_lines=("LIBRARY_DIR=" + lib,), **extra):
    with open(meminfo, "w") as f:
        f.write(f"MemTotal: 32000000 kB\nMemAvailable: {int(free_gb * 1024 * 1024)} kB\n")
    with open(env_file, "w") as f:
        f.write("\n".join(env_lines) + "\n")
    if os.path.exists(calls):
        os.remove(calls)
    env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_DOCKER_LOG=calls,
               FACTORY_MEMINFO=meminfo, FACTORY_INPUT=stack_input, FACTORY_ENV=env_file)
    env.pop("FACTORY_SERVICE", None)
    env.pop("LIBRARY_DIR", None)
    env.update(extra)
    done = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)
    logged = open(calls).read().splitlines() if os.path.exists(calls) else []
    return done, logged


ORCH = "python /app/orchestrator/orchestrator.py"

r, log = run("run", "--only", "genres")
check(f"run: the orchestrator in the ROCm service, one-off container (got {log})",
      r.returncode == 0 and f"compose --profile rocm run --rm ultrasinger-rocm {ORCH} run --only genres" in log)
check("run: docker compose runs inside stack/",
      open(calls + ".pwd").read().strip() == os.path.join(REPO, "stack"))
r, log = run("genres")
check(f"genres: = run --only genres (got {log})", f"compose --profile rocm run --rm ultrasinger-rocm {ORCH} run --only genres" in log)
r, log = run("list", "--match", "Rauta", FACTORY_SERVICE="ultrasinger")
check(f"FACTORY_SERVICE=ultrasinger: the CPU service, no profile (got {log})",
      f"compose run --rm ultrasinger {ORCH} list --match Rauta" in log)
r, log = run("list", env_lines=("LIBRARY_DIR=" + lib, "FACTORY_SERVICE=ultrasinger"))
check(f"FACTORY_SERVICE from stack/.env (got {log})", f"compose run --rm ultrasinger {ORCH} list" in log)
r, log = run("up")
check(f"up: a detached batch of all pending jobs (got {log})",
      "compose --profile rocm up -d ultrasinger-rocm" in log)
r, log = run("run", "--detach", "--only", "repairs")
check(f"run --detach: in the background as factory-run (got {log})",
      f"compose --profile rocm run -d --name factory-run ultrasinger-rocm {ORCH} run --only repairs" in log)
r, log = run("reset", "--done", "--match", "Song")
check(f"reset and its options are passed on (got {log})",
      f"compose --profile rocm run --rm ultrasinger-rocm {ORCH} reset --done --match Song" in log)
r, log = run("repair-one", "/data/input/repairs/A B")
check(f"repair-one keeps a path with spaces as one argument (got {log})",
      f"compose --profile rocm run --rm ultrasinger-rocm {ORCH} repair-one /data/input/repairs/A B" in log)

# guards
r, log = run("run", free_gb=3)
check(f"less than 5 GB free: nothing is started (got {r.returncode}: {r.stderr!r}, {log})",
      r.returncode != 0 and "GB" in r.stderr and not any(" run " in f" {l} " for l in log if "ps " not in l))
r, log = run("up", free_gb=3, FACTORY_MIN_FREE_GB="2")
check("FACTORY_MIN_FREE_GB lowers the limit", r.returncode == 0 and any("up -d" in l for l in log))
r, log = run("run", FAKE_STACK_RUNNING="1")
check(f"a running batch: no second container (got {r.returncode}: {r.stderr!r})",
      r.returncode != 0 and "already running" in r.stderr and not any(l.endswith(" run") for l in log))
r, log = run("report", FAKE_STACK_RUNNING="1")
check("a running batch: report/list/reset are refused too (they write state.json)",
      r.returncode != 0 and "already running" in r.stderr)
r, log = run("progress", "-w", FAKE_STACK_RUNNING="1")
check(f"a running batch: progress is read from it (got {log})",
      r.returncode == 0 and "compose --profile rocm exec ultrasinger-rocm progress -w" in log)
r, log = run("progress")
check(f"no batch: progress from a one-off container (got {log})",
      f"compose --profile rocm run --rm ultrasinger-rocm {ORCH} progress" in log)
r, log = run("run", FAKE_CHECK_RUNNING="1")
check(f"a running library check: no batch (got {r.stderr!r})",
      r.returncode != 0 and "library-check" in r.stderr)
r, log = run("run", FAKE_DOCKER_EXIT="137")
check(f"exit 137: reported as killed, likely out of memory (got {r.returncode}: {r.stderr!r})",
      r.returncode == 137 and "out of memory" in r.stderr)

# pulls (the real utilities, temp library and input)
r, log = run("pull-genres")
check(f"pull-genres: dry run of copy_genre_songs.py with LIBRARY_DIR (got {r.stdout!r})",
      r.returncode == 0 and "copy     ABBA - Chiquitita/ABBA - Chiquitita.txt" in r.stdout
      and "dry run" in r.stdout and not os.path.exists(os.path.join(stack_input, "genres")))
r, log = run("pull-genres", "--apply")
check("pull-genres --apply: the song file in input/genres/",
      r.returncode == 0 and os.path.isfile(os.path.join(stack_input, "genres", "ABBA - Chiquitita",
                                                       "ABBA - Chiquitita.txt"))
      and not os.path.exists(os.path.join(stack_input, "genres", "ABBA - Chiquitita", "a.mp3")))
r, log = run("pull-repairs", "--apply")
check(f"pull-repairs --apply: the broken.csv folder in input/repairs/ (got {r.stdout!r} {r.stderr!r})",
      r.returncode == 0 and os.path.isfile(os.path.join(stack_input, "repairs", "ABBA - Chiquitita", "a.mp3")))
r, log = run("pull-repairs", env_lines=())
check(f"no LIBRARY_DIR: the pull names the setting (got {r.stderr!r})",
      r.returncode != 0 and "LIBRARY_DIR" in r.stderr)
other_lib = os.path.join(root, "other")
os.makedirs(other_lib)
r, log = run("pull-genres", "--library", other_lib)
check(f"--library overrides LIBRARY_DIR (got {r.stdout!r})",
      r.returncode == 0 and "missing  ABBA - Chiquitita/ABBA - Chiquitita.txt" in r.stdout)

r, log = run("help")
check("help: lists the commands", r.returncode == 0 and "pull-genres" in r.stdout and "progress" in r.stdout)
r, log = run("bogus")
check("an unknown command: exit 2 and the help", r.returncode == 2 and "pull-repairs" in r.stdout + r.stderr)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
