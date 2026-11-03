#!/usr/bin/env bash
# About me: the one command line for the USDX Song Factory, run from the
# repository root on the host. It fills the stack input from the song
# library (pull-repairs, pull-genres: the host utilities, the library is
# only read) and runs every orchestrator command in the stack's container
# (docker compose from stack/). Full reference: docs/stack-manual.md.
#
#   ./factory.sh pull-repairs [--apply] [--library DIR]
#   ./factory.sh pull-genres  [--apply] [--library DIR]
#   ./factory.sh up                          a detached batch of all pending jobs
#   ./factory.sh run [--detach] [SELECTION]  run jobs (foreground; --detach: container factory-run)
#   ./factory.sh genres [SELECTION]          run the genre edits (= run --only genres)
#   ./factory.sh list|report [SELECTION]
#   ./factory.sh progress [-w]
#   ./factory.sh reset [--all | --done | --prune] [SELECTION]
#   ./factory.sh run-one URL | repair-one DIR
#   ./factory.sh logs
#   SELECTION = [--only generated,usdb,repairs,genres] [--match TEXT]
#
# Settings (environment first, then stack/.env): LIBRARY_DIR (the song
# library for the pulls), FACTORY_SERVICE (ultrasinger-rocm, the AMD GPU
# service, by default; ultrasinger for the CPU service), FACTORY_MIN_FREE_GB
# (5: free RAM a container start needs). Before starting a container the
# script checks that no stack batch and no library check is running (they
# share the state, the GPU and the CPU) and that enough RAM is free; a
# container killed with exit code 137 (usually out of memory) is named as
# such. Test hooks: FACTORY_ENV, FACTORY_INPUT, FACTORY_MEMINFO.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STACK="$ROOT/stack"
ENV_FILE="${FACTORY_ENV:-$STACK/.env}"
INPUT="${FACTORY_INPUT:-$STACK/input}"
MEMINFO="${FACTORY_MEMINFO:-/proc/meminfo}"
ORCH=(python /app/orchestrator/orchestrator.py)

# the value of KEY in stack/.env (last line wins, quotes removed), else ""
env_value() {
    [ -f "$ENV_FILE" ] || return 0
    local v
    v="$(grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    printf '%s' "$v"
}

SERVICE="${FACTORY_SERVICE:-$(env_value FACTORY_SERVICE)}"
SERVICE="${SERVICE:-ultrasinger-rocm}"
LIBRARY="${LIBRARY_DIR:-$(env_value LIBRARY_DIR)}"
MIN_FREE_GB="${FACTORY_MIN_FREE_GB:-$(env_value FACTORY_MIN_FREE_GB)}"
MIN_FREE_GB="${MIN_FREE_GB:-5}"
PROFILE=()
[ "$SERVICE" = "ultrasinger-rocm" ] && PROFILE=(--profile rocm)

die() { echo "factory.sh: $*" >&2; exit 1; }

usage() { sed -n '8,19p' "$0" | sed 's/^# \{0,1\}//'; }

compose() { (cd "$STACK" && docker compose "${PROFILE[@]}" "$@"); }

stack_running() { [ -n "$(compose ps --status running -q 2>/dev/null)" ]; }

free_gb() { awk '/^MemAvailable:/ { printf "%d", $2 / 1024 / 1024 }' "$MEMINFO"; }

# refuse a state-writing command while a batch runs (both would write
# state/state.json)
guard_batch() {
    if stack_running; then
        die "a stack batch is already running - watch it with ./factory.sh progress -w, " \
            "stop it from 'docker compose attach $SERVICE' with 'stop'"
    fi
}

# before a container that does real work: no batch, no library check, RAM
guard_start() {
    guard_batch
    if [ -n "$(docker ps --filter name=^library-check$ -q 2>/dev/null)" ]; then
        die "the library check (container library-check) is running - both need the GPU and the CPU"
    fi
    local free
    free="$(free_gb)"
    if [ -n "$free" ] && [ "$free" -lt "$MIN_FREE_GB" ]; then
        die "only $free GB RAM free, at least $MIN_FREE_GB GB needed (FACTORY_MIN_FREE_GB)"
    fi
}

# run a docker command, name an exit code 137 (killed: usually out of memory)
checked() {
    "$@"
    local code=$?
    if [ "$code" = 137 ]; then
        echo "factory.sh: the container was killed (exit code 137) - most likely out of memory;" \
             "see 'dmesg | grep -i oom' and the job's log in stack/logs/" >&2
    fi
    return "$code"
}

orchestrator() { checked compose run --rm "$SERVICE" "${ORCH[@]}" "$@"; }

# pull options: --apply, --library DIR
pull() {
    local tool="$1"; shift
    local apply=() lib="$LIBRARY"
    while [ $# -gt 0 ]; do
        case "$1" in
            --apply) apply=(--apply) ;;
            --library) shift; lib="${1:-}" ;;
            *) die "unknown option for this pull: $1" ;;
        esac
        shift
    done
    [ -n "$lib" ] || die "no library: set LIBRARY_DIR in stack/.env or give --library DIR"
    [ -d "$lib" ] || die "the library is not there: $lib"
    case "$tool" in
        repairs)
            [ -f "$INPUT/broken.csv" ] || die "no $INPUT/broken.csv"
            python3 "$ROOT/utilities/copy_reported_songs.py" --library "$lib" \
                --csv "$INPUT/broken.csv" --input "$INPUT" "${apply[@]}" ;;
        genres)
            [ -f "$INPUT/genre-edits.tsv" ] || die "no $INPUT/genre-edits.tsv"
            python3 "$ROOT/utilities/copy_genre_songs.py" --library "$lib" \
                --edits "$INPUT/genre-edits.tsv" --input "$INPUT" "${apply[@]}" ;;
    esac
}

cmd="${1:-help}"
[ $# -gt 0 ] && shift
case "$cmd" in
    pull-repairs) pull repairs "$@" ;;
    pull-genres) pull genres "$@" ;;
    up)
        guard_start
        compose up -d "$SERVICE" ;;
    run|genres)
        guard_start
        [ "$cmd" = genres ] && set -- --only genres "$@"
        if [ "${1:-}" = "--detach" ]; then
            shift
            if [ -n "$(docker ps -a --filter name=^factory-run$ --filter status=exited -q 2>/dev/null)" ]; then
                docker rm factory-run > /dev/null
            fi
            compose run -d --name factory-run "$SERVICE" "${ORCH[@]}" run "$@"
        else
            orchestrator run "$@"
        fi ;;
    list|report|reset)
        guard_batch
        orchestrator "$cmd" "$@" ;;
    run-one|repair-one)
        guard_start
        orchestrator "$cmd" "$@" ;;
    progress)
        if stack_running; then
            compose exec "$SERVICE" progress "$@"
        else
            orchestrator progress "$@"
        fi ;;
    logs) compose logs -f "$SERVICE" ;;
    help|-h|--help) usage ;;
    *) usage; exit 2 ;;
esac
