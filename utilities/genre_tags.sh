# About me: the #GENRE helpers apply_genres.sh and normalize_genres.sh share
# (sourced, not run): read a song file's #GENRE as UTF-8 and write a new one
# with a backup, keeping byte order mark, each line's ending and the
# encoding (a song file that is no valid UTF-8 - cp1252 in old libraries -
# gets the genre in cp1252), split a tab-separated line and unquote a cell
# LibreOffice saved.

# splits the tab-separated line $1 into the array CELLS, empty cells kept
# (read with IFS=tab would merge neighbouring tabs) and a CR at the end cut
split_tsv() {
    local line="${1%$'\r'}"
    CELLS=()
    IFS=$'\x1f' read -r -a CELLS <<< "${line//$'\t'/$'\x1f'}" || true
}

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
# added after the last header line; an empty $2 removes every #GENRE header
# line; every line keeps its own line ending
set_genre() {
    LC_ALL=C awk -v genre="$2" -v drop="$([ -n "$2" ] || echo 1)" '
        NR == 1 {
            bom = ""
            if (substr($0, 1, 3) == "\xef\xbb\xbf") { bom = substr($0, 1, 3); $0 = substr($0, 4) }
            eol = ($0 ~ /\r$/) ? "\r" : ""
            header = 1
            done = (drop == 1)
        }
        { cr = ($0 ~ /\r$/) ? "\r" : ""; line = $0; sub(/\r$/, "", line); pre = held ((NR == 1) ? bom : ""); held = "" }
        header && line !~ /^#/ {
            header = 0
            if (!done) { printf "#GENRE:%s%s\n", genre, eol; done = 1 }
        }
        header && toupper(substr(line, 1, 7)) == "#GENRE:" {
            if (!done) { printf "%s#GENRE:%s%s\n", pre, genre, cr; done = 1 } else held = pre
            next
        }
        { printf "%s%s%s\n", pre, line, cr }
        END { if (!done) printf "#GENRE:%s%s\n", genre, eol }
    ' "$1"
}

# the #GENRE value of a song file as UTF-8 (a cp1252 file converted)
read_genre() {
    local now
    now="$(current_genre "$1")"
    if iconv -f UTF-8 -t UTF-8 "$1" > /dev/null 2>&1; then
        printf '%s' "$now"
    else
        printf '%s' "$now" | iconv -f CP1252 -t UTF-8 2>/dev/null || true
    fi
}

# writes #GENRE $2 (UTF-8) into song file $1, after copying the file into
# backup folder $3 under its full path
write_genre() {
    local path="$1" genre="$2" backup="$3" tmp
    iconv -f UTF-8 -t UTF-8 "$path" > /dev/null 2>&1 \
        || genre="$(printf '%s' "$genre" | iconv -f UTF-8 -t CP1252//TRANSLIT)"
    mkdir -p "$backup$(dirname "$path")"
    cp -p "$path" "$backup$path"
    tmp="$(mktemp)"
    set_genre "$path" "$genre" > "$tmp"
    cat "$tmp" > "$path"
    rm -f "$tmp"
}
