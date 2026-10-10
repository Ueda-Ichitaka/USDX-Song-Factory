# Where songs and lyrics come from

For every new song the stack prefers the most trustworthy source: an existing
community-made song on USDB first, then real lyrics from the internet, and
only as a last resort the lyrics whisper heard in the audio.

## USDB

Before generating a new song from scratch, the stack checks whether a
matching upload already exists. There are **two** independent UltraStar song
databases, and both are checked:

1. **[usdb.animux.de](https://usdb.animux.de/)** - the original, much larger
   community database. Accessed via
   [usdb_syncer](https://github.com/bohning/usdb_syncer) (git submodule at
   `stack/usdb_syncer` - run `git submodule update --init --recursive` after
   cloning, and again after pulling an update to it). Tried first.
2. **[usdb.eu](https://usdb.eu/)** - a separate, newer database with its own
   accounts (not supported by usdb_syncer; `stack/orchestrator/usdb_eu_lookup.py`
   talks to it directly). Tried only when animux.de has no match.

A community-made, already-verified upload is almost always better than a
fresh generation, so it is preferred whenever a confident match exists.

### Setup

Each site needs its own free account, set in `stack/.env` (copy from
`.env.example`):

- animux.de: `USDB_USERNAME` / `USDB_PASSWORD`
- usdb.eu: `USDB_EU_EMAIL` / `USDB_EU_PASSWORD`

Leave a site's credentials unset to skip it; with both unset every new song
is generated from scratch.

### How a USDB song is built

1. Search animux.de's catalog (cached locally, refreshed every
   `USDB_CATALOG_TTL_HOURS` hours) for an artist+title match; if there is
   none, search usdb.eu. A match needs `USDB_MIN_MATCH_SCORE` (default 0.90)
   similarity on *both* artist and title - a great title match with the wrong
   artist is still the wrong song.
2. Download the song's notes (`.txt`), plus the cover art for animux.de
   matches.
3. Neither site hosts audio or video (copyright), so they are fetched with
   `yt-dlp`: an animux.de upload's own comment-linked YouTube video first (if
   it still resolves), otherwise the song list's own YouTube link (usdb.eu
   matches always use the song list's link).
4. The upload's `#GAP` was tuned for somebody else's audio file, so it is
   re-detected against the downloaded audio (`gap` repair mode - only
   `#GAP` changes, the community-made notes stay untouched). The new value
   is used only when the start values of the first words agree; otherwise
   the upload's `#GAP` is kept and the report lists the song under "Start
   kept by the gap check". `#GAP`
   corrections mentioned in animux.de comments are only logged into the
   txt's `#COMMENT`; they never override the re-detection.
5. The report's Lyrics column shows `usdb:animux:<song id>` or
   `usdb:eu:<song id>` for these songs.

Any failure on this path (no confident match, no usable video, a download
error, a site unreachable) is silent and non-fatal: the song is generated from
scratch as if USDB were not configured. The two sites are independent - a
broken account for one never blocks the other.

For animux.de only usdb_syncer's scraping module is reused (login, search,
song details and notes), not its downloader (which needs a desktop GUI event
loop). usdb.eu has no Python client, so `usdb_eu_lookup.py` talks to its
undocumented JSON endpoints directly (reverse-engineered from the site's
JavaScript and verified end to end). Details: the header comments of both
modules and `knowledge/02-DESIGN.md`.

## Online lyrics

Whisper transcribes what it *hears*, and sung words are hard to hear -
mis-heard words are the biggest lyrics-quality problem. So for every new song
that is generated (not pulled from USDB), the stack looks up real lyrics
before trusting the transcription:

1. **`lyrics_url` from the song list**, if given - used directly. A link
   (genius.com, an lrclib.net track page, a plain text or `.lrc` file on the
   web), or a lyrics file under `stack/input/` (such as
   `lyrics/Die Funkhausgruppe - Die Physiker.txt`) - see
   [new-songs.md](new-songs.md#optional-columns).
2. Otherwise all search sources are asked and the best-matching result wins
   (below a minimum confidence nothing is used):
   - **[syncedlyrics](https://pypi.org/project/syncedlyrics/)** - free, no
     API key. A web lookup that asks Musixmatch, LRCLIB, NetEase, Megalobiz
     and Genius one after another until one has the song.
   - **[Genius](https://genius.com/api-clients)** - only with a
     `GENIUS_API_KEY` in `stack/.env`; skipped without a key.

These lookups depend on websites. When a site answers with an error,
syncedlyrics reports "not found" without an error message (the log only shows
`LYRICS_NOT_FOUND`), so a song that had lyrics in one run can be transcribed in
the next. To make a song independent of the search, put its lrclib.net track
page in `lyrics_url` (find it on https://lrclib.net; the link has the form
`https://lrclib.net/tracks/<number>`), or save its lyrics as a file and put
the path in `lyrics_url`. The lrclib.net requests name the stack and link its
GitHub page (`User-Agent`), as lrclib.net requires.

Section markers from Genius (`[Chorus]`, `[Verse 1]`, also with a repeat count
such as `[Chorus] [x2]`) are removed; a bare repeated tag is replaced by that
section's text. A line entirely in round brackets, such as `(With a big heart
for the living room)`, is a backing voice sung at the same time as the lead
(a canon, the second voice of a duet, a choir call) and is left out; brackets
inside a line, as in `Oh (oh) my love`, stay. A `lyrics.txt` you put in the
song folder is used as it is.

When a source is found, its text is force-aligned to the separated vocals in
one pass over the whole song and REPLACES the transcription; the notes are
rebuilt from it and their pitches re-detected. When nothing is found, the song
keeps whisper's transcription - `output/report.md` flags these with a warning
mark so you know which songs are more likely to have mis-heard words. Disable the lookup
with `LYRICS_ENABLED=0`.

## Romanized lyrics

Lyrics in Cyrillic (Russian/Ukrainian), Korean (Hangul) or Japanese (kana/
kanji) are transliterated to Latin script right after a song is created or
repaired. This is a transliteration (via `cyrtranslit`, `korean-romanizer`,
`pykakasi`), not a translation: "Привет" becomes "Privet". Words already in
Latin script (loanwords, an English chorus) are left alone. Disable with
`ROMANIZE=0`.

Russian can be written in two styles, chosen with `ROMANIZE_STYLE` (see
[configuration.md](configuration.md)): `scientific` (the default - exact and
reversible, GOST 7.79 B: "Vo Rusal'i Dni vo lesny'h horomah") or `readable`
(karaoke style: "Vo Rusali Dni vo lesnykh khoromakh"). Ukrainian always
uses `scientific`. A finished song is already in Latin letters, so changing
the style does not touch it; its `lyrics.txt` keeps the Cyrillic text, so a
[lyrics repair](repairs.md#trusted-lyrics-lyrics-mode) of the song rebuilds it
in the new style.

To romanize a txt manually (from inside `stack/`):

```bash
docker compose run --rm ultrasinger python /app/orchestrator/romanize.py --txt "/data/output/new/Some Song/Some Song.txt"
```

## Spoken end-cards

A short, silence-isolated speech blurb near the end of a video ("thanks for
watching", ...) is dropped before it can end up in the lyrics. Tuned with
`ENDCARD_MIN_GAP_S` and `ENDCARD_MAX_DUR_S` (see
[configuration.md](configuration.md)).
