#!/bin/sh
# About me: starts library_check.py in the stack's container for one library.
#   run_library_check.sh <library folder> [output folder]
# The library is mounted read-only at the same path; the output folder
# (default: "library analysis" in the repository) gets results.jsonl and the
# report library-check.txt. Songs are checked in fresh worker processes and
# the check restarts itself if it is killed. Run it again to continue a
# stopped check (finished songs are skipped). Refuses to start while a check
# with the same container name is running.
#
# Settings (environment variables, defaults in brackets):
#   MEMORY (10g), MEMORY_SWAP (12g), CPUS (12)   caps of the container
#   THREADS (4)            CPU threads the aligner uses
#   MIN_AVAIL_GB (8)       wait before a song until this much RAM is free
#   MAX_LOAD (14)          ... and the 1-minute load is below this
#   WORKER_SONGS (10)      songs per fresh worker process
#   MAX_AUDIO_MIN (15)     leave out songs with longer audio
#   RETRY_ERRORS (unset)   1: check songs marked as errors again
#   CHECK_NAME (library-check)   container name
#   follow:  docker logs -f library-check      stop: docker stop library-check
set -e
[ -n "$1" ] || { echo "usage: $0 <library folder> [output folder]"; exit 1; }
LIB="$(cd "$1" && pwd)"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${2:-$REPO/library analysis}"
NAME="${CHECK_NAME:-library-check}"
MEMORY="${MEMORY:-10g}"; MEMORY_SWAP="${MEMORY_SWAP:-12g}"; CPUS="${CPUS:-12}"
THREADS="${THREADS:-4}"; MIN_AVAIL_GB="${MIN_AVAIL_GB:-8}"; MAX_LOAD="${MAX_LOAD:-14}"
WORKER_SONGS="${WORKER_SONGS:-10}"; MAX_AUDIO_MIN="${MAX_AUDIO_MIN:-15}"
if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; then
  echo "a check named $NAME is running - stop it first (docker stop $NAME) or set CHECK_NAME"
  exit 1
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
cd "$REPO/stack"
docker rm "$NAME" >/dev/null 2>&1 || true
docker compose run -d --no-deps --name "$NAME" \
  -e LIBRARY_DIR="$LIB" -e OMP_NUM_THREADS="$THREADS" -e THREADS="$THREADS" \
  -e MIN_AVAIL_GB="$MIN_AVAIL_GB" -e MAX_LOAD="$MAX_LOAD" -e WORKER_SONGS="$WORKER_SONGS" -e MAX_AUDIO_MIN="$MAX_AUDIO_MIN" \
  ${RETRY_ERRORS:+-e RETRY_ERRORS=1} \
  -v "$LIB:$LIB:ro" -v "$REPO/utilities:/data/utilities:ro" -v "$OUT:/data/analysis" \
  ultrasinger-rocm sh -c 'until python /data/utilities/library_check.py; do echo "check restarted"; sleep 30; done' >/dev/null
docker update --memory "$MEMORY" --memory-swap "$MEMORY_SWAP" --cpus "$CPUS" "$NAME" >/dev/null
echo "$NAME running for $LIB (memory $MEMORY, CPUs $CPUS), output in $OUT"
echo "follow: docker logs -f $NAME    stop: docker stop $NAME"
