#!/usr/bin/env bash
# About me: writes the genres genre_lookup.py predicted into the song files.
#   apply_genres.sh [--apply] [--backup DIR] [predictions file]
# Reads genre-predictions.tsv (default: "library analysis/genre-predictions.tsv"
# in the repository; tab-separated: band, title, path, predicted genre,
# found - edit the predicted genres before, an empty one is left out). For
# every row with a predicted genre whose song file still has no genre (no
# #GENRE, an empty one or the placeholder "Unknown"/"No idea"), the #GENRE
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

# a cell as LibreOffice may save it: surrounding double quotes removed
unquote() {
    local v="$1"
    if [ "${#v}" -ge 2 ] && [ "${v:0:1}" = '"' ] && [ "${v: -1}" = '"' ]; then
        v="${v:1:${#v}-2}"
        v="${v//\"\"/\"}"
    fi
    printf '%s' "$v"
}

# the #GENRE value of a song file's header (empty without one)
current_genre() {
    LC_ALL=C awk '
        NR == 1 { sub(/^\xef\xbb\xbf/, "") }
        { sub(/\r$/, "") }
        !/^#/ { exit }
        toupper(substr($0, 1, 7)) == "#GENRE:" { v = substr($0, 8); gsub(/^[ \t]+|[ \t]+$/, "", v); print v; exit }
    ' "$1"
}

# the song file with #GENRE set to $2 (bytes as they go into the file): the
# first #GENRE header line replaced (further ones dropped), else the line
# added after the last header line; every line keeps its own line ending
set_genre() {
    LC_ALL=C awk -v genre="$2" '
        NR == 1 {
            bom = ""
            if (substr($0, 1, 3) == "\xef\xbb\xbf") { bom = substr($0, 1, 3); $0 = substr($0, 4) }
            eol = ($0 ~ /\r$/) ? "\r" : ""
            header = 1
        }
        { cr = ($0 ~ /\r$/) ? "\r" : ""; line = $0; sub(/\r$/, "", line); pre = (NR == 1) ? bom : "" }
        header && line !~ /^#/ {
            header = 0
            if (!done) { printf "#GENRE:%s%s\n", genre, eol; done = 1 }
        }
        header && toupper(substr(line, 1, 7)) == "#GENRE:" {
            if (!done) { printf "%s#GENRE:%s%s\n", pre, genre, cr; done = 1 }
            next
        }
        { printf "%s%s%s\n", pre, line, cr }
        END { if (!done) printf "#GENRE:%s%s\n", genre, eol }
    ' "$1"
}

set_count=0; skipped=0; missing=0; empty=0
while IFS=$'\t' read -r band title path predicted _found || [ -n "${band:-}" ]; do
    band="$(unquote "$band")"; title="$(unquote "$title")"
    path="$(unquote "$path")"; predicted="$(unquote "${predicted:-}")"
    if [ -z "$predicted" ]; then
        empty=$((empty + 1)); continue
    fi
    if [ ! -f "$path" ]; then
        echo "missing   $path"; missing=$((missing + 1)); continue
    fi
    utf8=1
    iconv -f UTF-8 -t UTF-8 "$path" > /dev/null 2>&1 || utf8=0
    now="$(current_genre "$path")"
    [ "$utf8" = 1 ] || now="$(printf '%s' "$now" | iconv -f CP1252 -t UTF-8 2>/dev/null || true)"
    case "$(printf '%s' "$now" | tr '[:upper:]' '[:lower:]')" in
        ""|unknown|"no idea") ;;
        *) echo "has genre $band - $title: $now (left alone)"; skipped=$((skipped + 1)); continue ;;
    esac
    echo "set       $band - $title: $predicted"
    set_count=$((set_count + 1))
    [ "$APPLY" = 1 ] || continue
    genre="$predicted"
    [ "$utf8" = 1 ] || genre="$(printf '%s' "$predicted" | iconv -f UTF-8 -t CP1252//TRANSLIT)"
    mkdir -p "$BACKUP$(dirname "$path")"
    cp -p "$path" "$BACKUP$path"
    tmp="$(mktemp)"
    set_genre "$path" "$genre" > "$tmp"
    cat "$tmp" > "$path"
    rm -f "$tmp"
done < <(tail -n +2 "$TSV")

echo
echo "$set_count set, $skipped already with a genre, $empty without a prediction, $missing missing"
if [ "$APPLY" = 1 ]; then
    [ "$set_count" = 0 ] || echo "backup of the changed files: $BACKUP"
else
    echo "dry run - nothing written, use --apply"
fi
