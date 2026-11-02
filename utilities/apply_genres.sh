#!/usr/bin/env bash
# About me: writes the genres genre_lookup.py predicted into the song files.
#   apply_genres.sh [--apply] [--backup DIR] [predictions file]
# Reads genre-predictions.tsv (default: "library analysis/genre-predictions.tsv"
# in the repository; tab-separated: band, title, path, predicted genre,
# found - edit the predicted genres before, an empty one is left out). For
# every row with a predicted genre whose song file still has no genre (no
# #GENRE, an empty one or a placeholder: Unknown, No idea, Other, None,
# Misc), the #GENRE
# line is replaced in place, or added after the last header line. The rest
# stays: byte order mark, each line's ending (LF/CRLF; a last line without
# one gets one) and the encoding - a song file that is no valid UTF-8
# (cp1252 in old libraries) gets the genre in cp1252.
# Without --apply nothing is written, the script only lists what it would
# set. With --apply every changed file is copied first into the backup
# folder under its full path (default: "library analysis/genre-backup-<time>").
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0
BACKUP=""
TSV=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --backup) shift; BACKUP="${1:?--backup needs a folder}" ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) TSV="$1" ;;
    esac
    shift
done
TSV="${TSV:-$REPO/library analysis/genre-predictions.tsv}"
BACKUP="${BACKUP:-$REPO/library analysis/genre-backup-$(date +%Y%m%d-%H%M%S)}"
[ -f "$TSV" ] || { echo "no predictions file: $TSV" >&2; exit 1; }

# shellcheck source=genre_tags.sh
source "$(dirname "$0")/genre_tags.sh"

set_count=0; skipped=0; missing=0; empty=0
while IFS= read -r line || [ -n "$line" ]; do
    split_tsv "$line"
    band="$(unquote "${CELLS[0]:-}")"; title="$(unquote "${CELLS[1]:-}")"
    path="$(unquote "${CELLS[2]:-}")"; predicted="$(unquote "${CELLS[3]:-}")"
    if [ -z "$predicted" ]; then
        empty=$((empty + 1)); continue
    fi
    if [ ! -f "$path" ]; then
        echo "missing   $path"; missing=$((missing + 1)); continue
    fi
    now="$(read_genre "$path")"
    case "$(printf '%s' "$now" | tr '[:upper:]' '[:lower:]')" in
        ""|unknown|"no idea"|other|none|misc) ;;
        *) echo "has genre $band - $title: $now (left alone)"; skipped=$((skipped + 1)); continue ;;
    esac
    echo "set       $band - $title: $predicted"
    set_count=$((set_count + 1))
    [ "$APPLY" = 1 ] || continue
    write_genre "$path" "$predicted" "$BACKUP"
done < <(tail -n +2 "$TSV")

echo
echo "$set_count set, $skipped already with a genre, $empty without a prediction, $missing missing"
if [ "$APPLY" = 1 ]; then
    [ "$set_count" = 0 ] || echo "backup of the changed files: $BACKUP"
else
    echo "dry run - nothing written, use --apply"
fi
