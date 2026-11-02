# Library utilities

Tools to find songs with a wrong `#GAP` or drifting timing in an UltraStar
library and hand them to the stack for repair. Three steps, each its own
tool - run them one after another or only the ones you need - plus a tool
that copies the songs of a dashboard export into the stack input. The
repair itself is a normal stack run; step 4 covers getting the repaired
songs back into the library. Two more tools fill in missing `#GENRE` tags
(see [Genres](#genres)).

The stack input: `stack/input/` holds `broken.csv` and `song-requests.csv`,
`stack/input/repairs/` the song folders to repair, `stack/input/songtexts/`
lyrics files the csv rows point to, `stack/input/videos/` local song files
(see [Local song files](#local-song-files)).

All commands below run from the repository root (not from `stack/`).

| Step | Tool | Runs on | Changes |
|---|---|---|---|
| 1. Scan | `run_library_check.sh` (starts `library_check.py`) | the stack's container | nothing in the library; writes results + report |
| 2. Import | `import_broken_songs.py` | host | copies song folders into the stack input, adds `broken.csv` rows |
| 3. Lyrics links | `fill_lyrics_links.py` | host | fills empty `lyrics_url` cells of `broken.csv` |
| 4. Repair, copy back | the stack, a file manager | stack container, host | writes `stack/output/repaired/<repair>/`; you copy the songs into the library |
| Dashboard export | `copy_reported_songs.py` | host | copies the reported song folders into the stack input, merges the rows into `broken.csv` |
| Local song files | `import_local_media.py` | host | copies the files a song list names into `stack/input/videos/`, rewrites the list |
| Genres | `genre_lookup.py` | host | nothing in the library; writes the genre list and the predictions |
| Genres | `apply_genres.sh` | host | writes the predicted genres into the song files (backup kept) |
| Genre bases | `genre_clusters.py` | host | nothing in the library; writes each genre spelling with its proposed bases |
| Genre bases | `normalize_genres.sh` | host | rewrites `#GENRE` with the bases (backup kept) |

`songfile.py` is no tool of its own: the other tools use it to read song
file headers (the same rules as USDX) and to turn `#LANGUAGE`/`#GENRE` into
dashboard codes. `genre_bases.py` and `genre_tags.sh` are no tools either:
the genre tools share them (how a genre becomes bases; reading and writing
`#GENRE`).

Requirements:

- the scan: the stack's ROCm image (`ultrasinger-rocm`, built as described
  in `stack/docs/development.md`) and an AMD GPU - the check runs the vocal
  separation and the aligner on the GPU (the service maps `/dev/kfd` and
  `/dev/dri`); Docker with the Compose plugin
- the host tools: Python 3 with `requests`, and `ffprobe` (ffmpeg) for the
  audio length checks; `apply_genres.sh` and `normalize_genres.sh`: bash,
  GNU awk and iconv (both use `genre_tags.sh`)

## 1. Scan a library

```sh
utilities/run_library_check.sh /path/to/library [output folder]
```

Every usable song file is checked the way the stack's gap/sync repair
measures a song: its own lyrics are aligned to its separated vocals, which
gives how far the lyrics are off at the first line, how much that grows
over the song (drift), how many lines agree, and the `#GAP` (and `#BPM`)
that would fix it. Song files are `.txt` files with `#TITLE` and `#ARTIST`
(as in USDX; `license.txt`, `README.txt` and notes are not songs). Left out:
duets, relative timing (`#RELATIVE:yes` - as in USDX, a line break like
`- 30 31` alone is absolute timing), song files without `#BPM` (USDX does
not load them), songs without audio or with fewer than 50 notes, songs this
stack generated, audio longer than 15 minutes.

- The library is mounted read-only - nothing in it is changed.
- Output folder (default `library analysis/` in the repository):
  `results.jsonl` (one line per song file) and `library-check.txt` (the
  report: `#GAP off`, `drifts`, `unclear`, `good`, left out), rewritten
  after every 10 songs.
- About 20-45 s per song; a whole library takes many hours. Songs are
  checked in a random order, so a partial run is already a fair sample.
- Stop with `docker stop library-check`, continue by running the same
  command again (finished songs are skipped; a result that no longer fits
  the song file - e.g. a song left out for a reason that no longer holds -
  is redone, and the report only lists the library's current song files). The script refuses to start
  while a check is running.
- Follow the progress: `docker logs -f library-check`.
- Resources: the container is capped (`MEMORY`, `CPUS` below) and waits
  before each song until enough RAM is free and the load is low enough
  (`MIN_AVAIL_GB`, `MAX_LOAD`). Do not run it at the same time as a stack
  batch: both use the GPU and the CPU-heavy aligner.

Settings (environment variables set when calling the script - there is
no settings file):

| Variable | Default | Meaning |
|---|---|---|
| `MEMORY` | `10g` | memory cap of the container - an overrun only stops the check, not your other programs |
| `MEMORY_SWAP` | `12g` | memory + swap cap |
| `CPUS` | `12` | CPU cap of the container |
| `THREADS` | `4` | CPU threads of the aligner |
| `MIN_AVAIL_GB` | `8` | before each song, wait until this much RAM is free |
| `MAX_LOAD` | `14` | ... and the 1-minute load is below this |
| `WORKER_SONGS` | `10` | songs per fresh worker process (memory is given back after each) |
| `MAX_AUDIO_MIN` | `15` | leave out songs with longer audio (live sets, medleys need more memory than the cap) |
| `RETRY_ERRORS` | unset | `1`: check songs marked as errors again (each once per run; a song that fails again stays marked) |
| `CHECK_NAME` | `library-check` | container name (for a second check at the same time) |
| `ONLY` | unset | only songs whose name contains this text - for tests of `library_check.py` in the container; `run_library_check.sh` does not pass it on |

Example: `MEMORY=8g CPUS=6 utilities/run_library_check.sh /music/ultrastar`

Verdicts in the report:

- **#GAP off** - shifted by a constant amount; the given `#GAP` fixes it.
- **drifts** - the shift grows over the song: `#BPM` is wrong or the audio
  is another version; the given `#GAP` and `#BPM` fix it.
- **unclear** - fewer than 60% of the lines fit: a different audio version,
  or singing the aligner cannot follow. Listen before changing anything.
- **damaged audio** - the audio decodes to less than half of its stated
  length (a zero-filled or broken file); replace the file.
- **good** - the lyrics sit on the singing; nothing to do.

A song is named by its folder; when the song file's name differs from the
folder, it follows in brackets (`ABBA - Chiquitita [Chiquitita-402]`). The
left-out list at the end of the report gives every left-out song with its
reason - a song whose check crashed is listed there as `error: ...`
(`RETRY_ERRORS=1` checks those again).

`results.jsonl` has one JSON line per checked song file (the newest line
per name counts). A checked song has `name`, `lang`, `lines`, `gap_ms`,
`bpm`, `offset_s` (song file minus singing, negative = the lyrics come too
late), `drift_s`, `agree`, `gap_fix_ms`, `bpm_fix`, `gap_fix_with_bpm_ms`
and `verdict` (plus `note` when there is one); a left-out song has `name`
and `skip` (the reason). Step 2 reads this file.

## 2. Import the songs into the stack

```sh
python3 utilities/import_broken_songs.py --library /path/to/library            # dry run
python3 utilities/import_broken_songs.py --library /path/to/library --apply
```

For every song with the verdict `#GAP off` (category `gap`) or `drifts`
(category `async`) of the scan's `results.jsonl`:

- its folder is copied from the library into `stack/input/repairs/` (a
  folder already there is left as it is);
- a `broken.csv` row is added in the karaoke-dashboard's export format:
  band and song name from the song file's `#ARTIST`/`#TITLE` (the stack
  matches rows to folders by them), the measured values and the fix as
  description (e.g. `library check: lyrics 9.76 s too late; fix #GAP 30270
  -> 20513`), trait `none` (set traits in the dashboard), no `cover_url`
  (library songs have their cover). Language: the dashboard code of the
  first `#LANGUAGE` entry; empty for romanized lyrics ("Japanese
  (romanized)") and unknown values, so the stack detects it. Genre: `#GENRE`
  when it is exactly a dashboard genre code, else `default`. A song that already has
  a row gets no second one; an older `broken.csv` is rewritten in the
  current format (a backup `broken.csv.bak-<time>` is kept);
- then step 3 runs for the rows without a lyrics link (`--no-lyrics` skips it).

Options: `--results FILE` (default `library analysis/results.jsonl`),
`--input DIR` (the stack input, default `stack/input`; the folders go to its
`repairs/`), `--categories gap`, `drift` or
`gap,drift` (default both).

Note: a `broken.csv` exported from the dashboard later does not contain
these rows unless the songs are added there too.

## 3. Lyrics links

```sh
python3 utilities/fill_lyrics_links.py            # dry run
python3 utilities/fill_lyrics_links.py --apply
```

Looks up a link for every `broken.csv` row with an empty `lyrics_url`
(rows with a link are never touched):

- **lrclib.net** first: artist and title must match closely, and the
  hit's length must be within 4 s of the song's own audio in
  `stack/input/repairs/` (same version); synced lyrics are preferred.
  Link: `https://lrclib.net/tracks/<id>`.
- **Genius** when lrclib.net has nothing and a Genius API key is set: the
  environment variable `GENIUS_API_KEY`, else the same key in the stack's
  own `stack/.env` (the key its lyrics search uses, see
  `stack/.env.example`). The utilities have no settings file of their own.
- **lrclib.net free-text search** when both find nothing (the artist is
  often named differently there, e.g. "Der König der Löwen" for "Disney's
  Der König der Löwen"): by artist and title, then by the title alone. A
  hit counts when its title matches, its length is within 4 s of the song's
  own audio and a word of the band (not "the", "and", "feat") is in the
  hit's artist or title - so "Queen - Jealousy" is not taken for "Marina
  And The Diamonds - Jealousy". Without the song's audio nothing is taken.

Options: `--csv FILE` (default `stack/input/broken.csv`), `--input DIR`
(the folder of song folders, default: `repairs/` next to the csv). The requests identify
the stack (User-Agent) and wait 1 s between songs; when lrclib.net is
overloaded (429/503) a request is tried again after 5, 10 and 20 s.

## 4. Repair and copy back into the library

Start the stack on the imported songs (from `stack/`, see
`stack/docs/running.md`); to run only the repairs:

```sh
cd stack
docker compose --profile rocm run -d --name repairs-run ultrasinger-rocm \
  python /app/orchestrator/orchestrator.py run --only repairs
docker exec -it repairs-run progress -w      # live progress
```

The repaired songs land in `stack/output/repaired/<repair>/<song folder>/`,
one folder per repair actually done: `gap/`, `sync/`, `recreated/` (a sync
repair that had to re-create the song from the audio), `lyrics/`, `media/`,
`cover/`. Each is a full copy of the song folder with the fixed song file.

Copying them into the library (the library is not written by any tool
here): the media files keep the modification time of the library's
files (the copies into the input and the repair preserve it), the fixed
song file is newer. Folders that came into the input another way may have
newer media times; those files are copied again, which does no harm. In Dolphin (KDE), drop the song
folders onto the library, choose "Write Into" for the folders, tick "Apply
to All" and pick "Overwrite older files" from the Overwrite button's menu:
only files newer than the library's are copied, the unchanged audio and
video are skipped. Files the library does not have are copied anyway:

- `language.txt` in every repaired folder (the language the repair used;
  USDX ignores it);
- in `recreated/`: `[Vocals].mp3`, `[Instrumental].mp3`, a `.mid`, and in
  some folders a second song file next to the old one - USDX would list
  that song twice, so check these folders by hand;
- a folder whose name is not in the library is added as a new song.

## Songs reported in the dashboard

```sh
python3 utilities/copy_reported_songs.py --library /path/to/library --csv ~/Downloads/broken.csv            # dry run
python3 utilities/copy_reported_songs.py --library /path/to/library --csv ~/Downloads/broken.csv --apply
```

For every row of a karaoke-dashboard export ("Reported" page, `broken.csv`,
with or without the header line; `--csv` can be given more than once):

- the song folder is found in the library the way the dashboard names it:
  `<band> - <song name>` (just `<song name>` without a band), ignoring case;
- the folder is copied into `stack/input/repairs/` (a folder already there,
  in any case, is left as it is); rows without a library folder are listed;
- the row is merged into `stack/input/broken.csv` (dashboard format, a
  backup `broken.csv.bak-<time>` is kept); a song that already has a row
  gets no second one. Rows without a library folder are merged too (the
  stack ignores a row without a folder). Empty genre and trait cells become
  `default` and `none`.

Option: `--input DIR` (the stack input, default `stack/input`; the folders go
to its `repairs/`).

## Local song files

```sh
python3 utilities/import_local_media.py            # dry run
python3 utilities/import_local_media.py --apply
```

A song-request row may name an audio or video file instead of a YouTube
link (a song that is not on YouTube). The stack runs in a container that
only sees its input folder, so this tool copies every such file into
`stack/input/videos/` and writes `videos/<file name>` into the row. Run it
after saving a fresh dashboard export; links, `skip` rows and paths already
in the input stay (an absolute path into the input, or `/data/input/...`,
is made relative).

- Accepted: `.mp3 .m4a .wav .ogg .flac .opus` and `.mp4 .webm .mkv .avi
  .mov` (the stack's list). A folder, a missing file or another type is
  listed as an error and the row stays; the stack then fails that song
  with the reason.
- The file keeps its name. A file of the same name and size in
  `input/videos/` counts as copied before and is reused; a different file
  of that name gets ` (1)`. The copy keeps the file's date.
- With `--apply` the song list is rewritten (a backup
  `song-requests.csv.bak-<time>` is kept), without it the tool only lists
  what it would do.

Where a path is read from:

| Path in the song list | Read from | Tested |
|---|---|---|
| `videos/x.mp4`, `/data/input/...`, an absolute path inside the input | already in the input | ✅ |
| `/any/absolute/path.mp4` | that path (local disk, a mounted share) | ✅ |
| `smb://host/share/...`, `//host/share/...`, `\\host\share\...` on a mounted share | the share's mount point (`/proc/self/mounts`; host name or IP, share name in any case) | ✅ fstab CIFS mounts |
| `nfs://host/export/...` on a mounted export | the export's mount point | ❌ reference |
| `smb://`, `nfs://`, UNC on a share that is not mounted | `gio copy` (the desktop's network layer and stored logins) | ❌ reference |
| anything named in `--map PREFIX=DIR` (e.g. `--map 'Z:\=/dhuaddan/video/'` for a Windows drive) | `DIR` + the rest of the path | ✅ |

The two **reference** paths are written but not tested against a real
server (there are no NFS mounts or unmounted shares on the machine this was
built on) - open for contribution: whoever has such a setup, please test
them and fix or extend `_nfs_mount()` and `gio_copy()` in
`import_local_media.py`. A Windows drive letter without `--map` is an
error, since its mount point here cannot be known.

## Genres

[genre-workflow.html](genre-workflow.html) shows the four genre tools in one
picture - what each reads and writes, your review steps and the steps that
change the library (open it in a browser).

```sh
python3 utilities/genre_lookup.py --library /path/to/library     # writes the two lists, changes nothing
utilities/apply_genres.sh                                         # dry run: what would be set
utilities/apply_genres.sh --apply                                 # writes #GENRE into the song files
```

`genre_lookup.py` reads every song file of the library (all subfolders) and
writes into `library analysis/` (`--out DIR`):

- `library-genres.tsv`: every song - band, title, genre, path;
- `genre-predictions.tsv`: the songs without a genre - no `#GENRE`, an empty
  one, or a placeholder: `Unknown`, `No idea`, `Other`, `None`, `Misc`
  (`--keep-placeholders` counts these as a genre) - with band, title, path,
  predicted genre, found (each source's own genres, for checking) and new
  bases (predicted bases the library does not have yet - add a rule in
  `genre_bases.py` or edit the prediction before `apply_genres.sh`).

The sources, per song without a genre:

- **MusicBrainz**: the song's recording (the one with that exact title and
  the most releases - a song has many live versions and edits), its album
  when the recording has no genres, the artist when neither has; genres by
  votes. One request every 1.5 s.
- **Metal Archives**: the band, only when exactly one band has that exact
  name (several bands are called Avatar). An unofficial search page; it
  answers automated requests with a block page (HTTP 403) at times - then it
  is left out for the rest of the run and the run says so.
- **Wikidata**: the artist (same name, described as a band, singer, group,
  unit, ...) and its genres.
- **Last.fm**: the artist's top tags, only with an API key
  (`LASTFM_API_KEY` in the environment or `stack/.env`; free at
  https://www.last.fm/api/account/create); only tags that are MusicBrainz
  genres count. `--no-lastfm` leaves it out.

The predicted genre: each found genre goes the same way as the library's
genres (see [Genre bases](#genre-bases)): when `library analysis/genre-clusters.tsv`
exists, a found genre with the spelling of a row (any case: `power metal` =
`Power Metal`) gets that row's genre cell - your reviewed proposals, `-`
drops it; any other found genre goes through the rules of `genre_bases.py`
(`symphonic black metal` -> `Symphonic, Black, Metal`, `medieval rock` ->
`Mittelalter, Rock`, `easy listening` -> dropped). The bases most sources
agree on come first, all of them. A song in `utilities/genre-song-fixes.tsv`
(`--fixes FILE`) gets its fix as prediction (found: `song fix`) and is not
looked up. Run `genre_clusters.py` and review its file first, so the
lookup uses your edits.
Rate-limit answers (429/503) are asked again after 5, 15 and 30 s.
Answers are cached in `library analysis/genre-cache.json`, so a second run
only asks for new songs (a failed request is asked again). A library of 750
songs without a genre (about 280 artists) takes about 45 minutes.

Check the predictions before writing them: edit the `predicted genre`
column (LibreOffice: open as tab-separated text, save in the same format);
an empty cell leaves that song alone.

`apply_genres.sh [--apply] [--backup DIR] [predictions file]` writes
`#GENRE` into each listed song file that still has no genre (a song that got
one in the meantime is left alone): the placeholder line is replaced in
place, else the line is added after the last header line. Byte order mark,
line endings and the encoding stay (a cp1252 song file gets the genre in
cp1252). A song file that is not writable - the read-only attribute set on
the NAS - is listed as `read-only` and skipped, the run goes on; clear the
attribute (File Station, Windows file properties) and run again. Without
`--apply` it only lists what it would set. With `--apply`
every changed file is copied first into `library analysis/genre-backup-<time>/`
under its full path.

### Genre bases

Every genre is broken down into its bases, so a library with hundreds of
genre combinations ends up with a short list of bases (and a genre map in
the dashboard stays readable): `Alternative Metal` -> `Alternative, Metal`,
`Symphonic Black Metal` -> `Symphonic, Black, Metal`, `Mittelalter-Rock` ->
`Mittelalter, Rock`.

```sh
python3 utilities/genre_clusters.py --library /path/to/library   # writes the proposals, changes nothing
utilities/normalize_genres.sh                                     # dry run: what would change
utilities/normalize_genres.sh --apply                             # rewrites #GENRE in the song files
```

The rules live in `genre_bases.py` (the lists there can be edited); the
genre lookup uses the same rules:

- a genre is split at spaces, hyphens and slashes;
- names in `KEEP_WHOLE` stay one base in any spelling: `Hip Hop`, `hiphop`
  -> `Hip-Hop`; `A capella` -> `A cappella`; also R&B, K-Pop, J-Pop,
  J-Rock, Drum and Bass, Neue Deutsche Härte, New Wave, Hi-NRG, EBM, ...;
- names in `DROPPED` are no genre for this library and are removed wherever
  they appear: Easy Listening, Singer-Songwriter. `PHRASES` turn a name
  into other bases: Video Game Music -> Soundtrack, 8bit/8-bit -> Electronic,
  Grog'n Roll -> Pirate,
  Garage Rock -> Rock, Skate Punk -> Punk, US Metal -> Metal, Synthpop ->
  Pop, Eurodance -> Dance, Tanzmetal -> Dance, Metal, Darkwave -> Gothic,
  Electro Hop -> Electronic, Hip-Hop;
- filler words (`FILLER`: music, general, and, &, ...) are dropped:
  `Electronic Music` -> `Electronic`;
- translations and short forms: `Alternativo` -> Alternative, `Sinfonico`
  -> Symphonic, `BSO` -> Soundtrack, `Alt.` -> Alternative, `Goth` ->
  Gothic. `Mittelalter` stays German (MusicBrainz's `medieval` stays
  Medieval);
- a one-word genre splits into two halves when both halves are genres
  themselves - a one-word MusicBrainz genre or a base in the library:
  `Electropop` -> Electronic, Pop. `Hardcore` stays (`core` is no genre).
  `KEEP_ONE_WORD` lists one-word genres that never split;
- typos: a word that is not in the
  [MusicBrainz genre list](https://musicbrainz.org/ws/2/genre/all?fmt=txt)
  and has exactly one close word in it that the library also uses
  (`Ghotic` -> Gothic, `Trash` -> Thrash). Real genres close to each other
  (`Punk`, `Funk`) stay apart. The list is downloaded once into
  `library analysis/musicbrainz-genres.txt` (delete it to get a new one);
- last, `MERGE` turns a base into other bases or none, wherever it came
  from: Electro -> Electronic (also the half of `Electropop`),
  Metalcore -> Metal, Rapcore -> Rap, Rock and Roll -> Rock, Christmas and
  Vocal -> Pop, Folklore and Shanty -> Folk, Musical -> Soundtrack, Ragga
  -> Reggae, Medieval -> Mittelalter, Celtic -> Pagan, Pony -> Children;
  Gypsy and Freak are dropped (`Gypsy jazz` -> Jazz, `Freak Metal` ->
  Metal).

Single songs whose genre no rule gets right go into
`utilities/genre-song-fixes.tsv` (hand-written, in the repository, never
written by a tool; tab-separated: band, title, genre, note). The genre is
the song's whole new `#GENRE` value; band and title match in any case, so a
song's duet version is fixed too. Example: Twisted Sister's "Heavy Metal
Christmas" was tagged `Christmas` by its USDB uploader and gets `Metal`.
`genre_clusters.py` counts the fixes in its overview and summary
(`--fixes FILE`), `normalize_genres.sh` writes them (`--fixes FILE`; marked
`(song fix)` in its list), also into a song that had no genre.

A base is written the way most library songs write it (first letter upper
case). The bases of a song keep their order of appearance, duplicates
dropped: `Gothic Rock, Symphonic Black Metal, Gothic Metal` -> `Gothic,
Rock, Symphonic, Black, Metal`. The placeholders above are left out.

`genre_clusters.py` writes into `library analysis/` (`--out DIR`):

- `genre-clusters.tsv`: one row per spelling - genre (its proposed bases),
  spelling, songs, why (`unchanged`, `spelling`, `split`, `kept whole: ...`,
  `translation ...`, `synonym ...`, `filler dropped: ...`, `compound ...`,
  `typo? ...`);
- `genre-overview.tsv`: one row per base - base, songs, the spellings it
  comes from with their song counts;
- `library-genres.tsv`: every song, as above.

Check `genre-clusters.tsv` before using it: change a `genre` cell to the
bases you want (comma-separated), `-` to drop that spelling from the songs,
or empty it to leave that spelling as it is.

`normalize_genres.sh [--apply] [--backup DIR] [--songs FILE] [--fixes FILE] [clusters file]`
goes through the songs of `library-genres.tsv` (`--songs`) and, for each song
with a genre, replaces every comma-separated part by its bases from the
clusters file, drops duplicates (any case) and joins the bases with `, `.
Parts the file does not name stay. A song left without any base loses its
`#GENRE` line (the genre lookup then counts it as without a genre). A song without a genre or with a
placeholder is left alone. Byte order mark, line endings and the encoding
stay. A read-only song file is listed as `read-only` and skipped, as with
`apply_genres.sh`. Without `--apply` it only lists what it would change (`old -> new`).
With `--apply` every changed file is copied first into
`library analysis/genre-normalize-backup-<time>/` under its full path.

Run it after `apply_genres.sh`, so the genres just added are checked too.

## Tests

```sh
cd utilities
python3 test_songfile.py
python3 test_library_check.py
python3 test_import_broken_songs.py
python3 test_fill_lyrics_links.py
python3 test_copy_reported_songs.py
python3 test_genre_lookup.py
python3 test_apply_genres.py
python3 test_genre_bases.py
python3 test_genre_clusters.py
python3 test_normalize_genres.py
python3 test_import_local_media.py
```
