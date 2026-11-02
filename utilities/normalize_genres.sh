#!/usr/bin/env bash
# About me: rewrites the existing #GENRE tags of a library with their
# bases, from the genre-clusters.tsv genre_clusters.py wrote.
#   normalize_genres.sh [--apply] [--backup DIR] [--songs FILE] [--fixes FILE]
#                       [clusters file]
# The clusters file (default: "library analysis/genre-clusters.tsv";
# tab-separated: genre, spelling, songs, why) maps each spelling to its
# bases ("Black, Metal"; "-" drops the spelling) - edit the genre cells
# before, an empty one leaves that spelling as it is. The song files are the path column of the song
# list (--songs, default: "library analysis/library-genres.tsv"). For every
# song with a genre, the #GENRE value is split at commas, each part
# replaced by its bases, duplicates (any case) dropped and the bases joined
# with ", "; when that differs from the value, the #GENRE line is rewritten
# (removed when no base is left). No genre and a
# placeholder (Unknown, No idea, Other, None, Misc) are left alone. A song
# in the song fixes file (--fixes, default: utilities/genre-song-fixes.tsv;
# band, title, genre, note; band and title in any case) gets that genre as
# its whole value instead, also without a genre before. Byte
# order mark, line endings and the encoding stay (genre_tags.sh). A song
# file that is not writable (the read-only attribute on the NAS) is listed
# as "read-only" and skipped; the run goes on.
# Without --apply nothing is written, the script only lists what it would
# change. With --apply every changed file is copied first into the backup
# folder under its full path (default:
# "library analysis/genre-normalize-backup-<time>").
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0
BACKUP=""
CLUSTERS=""
SONGS=""
FIXES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --backup) shift; BACKUP="${1:?--backup needs a folder}" ;;
        --songs) shift; SONGS="${1:?--songs needs a file}" ;;
        --fixes) shift; FIXES="${1:?--fixes needs a file}" ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) CLUSTERS="$1" ;;
    esac
    shift
done
CLUSTERS="${CLUSTERS:-$REPO/library analysis/genre-clusters.tsv}"
SONGS="${SONGS:-$REPO/library analysis/library-genres.tsv}"
FIXES="${FIXES:-$REPO/utilities/genre-song-fixes.tsv}"
BACKUP="${BACKUP:-$REPO/library analysis/genre-normalize-backup-$(date +%Y%m%d-%H%M%S)}"
[ -f "$CLUSTERS" ] || { echo "no clusters file: $CLUSTERS" >&2; exit 1; }
[ -f "$SONGS" ] || { echo "no song list: $SONGS" >&2; exit 1; }

# shellcheck source=genre_tags.sh
source "$(dirname "$0")/genre_tags.sh"

declare -A GENRE_OF=()
while IFS= read -r line || [ -n "$line" ]; do
    split_tsv "$line"
    genre="$(unquote "${CELLS[0]:-}")"; spelling="$(unquote "${CELLS[1]:-}")"
    [ -n "$genre" ] && [ -n "$spelling" ] && GENRE_OF["$spelling"]="$genre"
done < <(tail -n +2 "$CLUSTERS")

declare -A FIX_OF=()
if [ -f "$FIXES" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        split_tsv "$line"
        band="$(unquote "${CELLS[0]:-}")"; title="$(unquote "${CELLS[1]:-}")"
        genre="$(unquote "${CELLS[2]:-}")"
        [ -n "$genre" ] && FIX_OF["${band,,}"$'\t'"${title,,}"]="$genre"
    done < <(tail -n +2 "$FIXES")
fi

# trims spaces and tabs around $1
trim() {
    local v="$1"
    v="${v#"${v%%[![:space:]]*}"}"
    printf '%s' "${v%"${v##*[![:space:]]}"}"
}

# the value $1 with each comma-separated part replaced by its bases,
# duplicates (any case) dropped
normalized() {
    local -A seen=()
    local out="" part base key
    local -a parts bases
    IFS=',' read -r -a parts <<< "$1"
    for part in "${parts[@]}"; do
        part="$(trim "$part")"
        [ -n "$part" ] || continue
        IFS=',' read -r -a bases <<< "${GENRE_OF["$part"]:-$part}"
        for base in "${bases[@]}"; do
            base="$(trim "$base")"
            key="${base,,}"
            [ -n "$base" ] && [ "$base" != "-" ] && [ -z "${seen["$key"]:-}" ] || continue
            seen["$key"]=1
            out="${out:+$out, }$base"
        done
    done
    printf '%s' "$out"
}

changed=0; unchanged=0; without=0; missing=0; readonly=0
while IFS= read -r line || [ -n "$line" ]; do
    split_tsv "$line"
    band="$(unquote "${CELLS[0]:-}")"; title="$(unquote "${CELLS[1]:-}")"
    path="$(unquote "${CELLS[3]:-}")"
    if [ ! -f "$path" ]; then
        echo "missing   $path"; missing=$((missing + 1)); continue
    fi
    now="$(trim "$(read_genre "$path")")"
    fix="${FIX_OF["${band,,}"$'\t'"${title,,}"]:-}"
    if [ -n "$fix" ]; then
        new="$(trim "$fix")"; how=" (song fix)"
    else
        case "${now,,}" in
            ""|unknown|"no idea"|other|none|misc) without=$((without + 1)); continue ;;
        esac
        new="$(normalized "$now")"; how=""
    fi
    if [ "$new" = "$now" ]; then
        unchanged=$((unchanged + 1)); continue
    fi
    if [ ! -w "$path" ]; then
        echo "read-only $band - $title: ${now:-(no genre)} -> ${new:-(no genre, line removed)}$how"
        readonly=$((readonly + 1)); continue
    fi
    echo "change    $band - $title: ${now:-(no genre)} -> ${new:-(no genre, line removed)}$how"
    changed=$((changed + 1))
    [ "$APPLY" = 1 ] || continue
    write_genre "$path" "$new" "$BACKUP"
done < <(tail -n +2 "$SONGS")

echo
echo "$changed changed, $unchanged unchanged, $without without a genre, $missing missing, $readonly read-only"
if [ "$APPLY" = 1 ]; then
    [ "$changed" = 0 ] || echo "backup of the changed files: $BACKUP"
    [ "$readonly" = 0 ] || echo "read-only files were not changed: clear their read-only attribute on the NAS and run again"
else
    echo "dry run - nothing written, use --apply"
fi
