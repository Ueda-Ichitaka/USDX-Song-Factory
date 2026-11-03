# Stack manual

Man-page style reference of the USDX Song Factory's commands and settings:
`factory.sh` in the repository root and what it runs in the stack. A
task-oriented introduction is in [running.md](running.md), the whole
workflow in one picture in [factory-workflow.html](factory-workflow.html),
the orchestrator's own reference (what each command does inside the
container) in [orchestrator-manual.md](orchestrator-manual.md).

```text
FACTORY.SH(1)                 USDX Song Factory                 FACTORY.SH(1)

NAME
    factory.sh - fill the stack input from the song library and run the
    stack's batch jobs (new songs, repairs, genre edits)

SYNOPSIS
    ./factory.sh pull-repairs [--apply] [--library DIR]
    ./factory.sh pull-genres  [--apply] [--library DIR]
    ./factory.sh up
    ./factory.sh run [--detach] [SELECTION] [--no-usdb]
    ./factory.sh genres [SELECTION]
    ./factory.sh list | report [SELECTION]
    ./factory.sh progress [-w | --watch]
    ./factory.sh reset [--all | --done] [SELECTION]
    ./factory.sh reset --prune [SELECTION]
    ./factory.sh run-one URL | FILE
    ./factory.sh repair-one DIR
    ./factory.sh logs
    ./factory.sh help

    SELECTION = [--only CATEGORY[,CATEGORY...]] [--match TEXT]
    CATEGORY  = generated | usdb | repairs | genres

DESCRIPTION
    factory.sh runs on the host, from the repository root. The pull commands
    copy songs from the song library (LIBRARY_DIR) into stack/input/; they
    run the host utilities and only read the library. Every other command
    runs the orchestrator (stack/orchestrator/orchestrator.py) in the
    stack's container with docker compose, from stack/: a one-off container
    that is removed when it ends, or the detached batch container (up,
    run --detach). The stack never writes to the library: its results go
    to stack/output/, from where you copy them in.

    One job at a time, in one queue: a new song per row of
    input/song-requests.csv, a repair per folder in input/repairs/, a genre
    edit per row of input/genre-edits.tsv. Every job's status is kept in
    state/state.json; finished jobs are skipped, so a stopped run continues
    where it left off.

COMMANDS
    pull-repairs [--apply] [--library DIR]
        For every row of input/broken.csv, copy the song folder
        "<band> - <song name>" (any case) from the library into
        input/repairs/ (utilities/copy_reported_songs.py). A folder already
        there is left as it is; rows without a library folder are listed.
        input/broken.csv itself is not changed. Without --apply only lists.

    pull-genres [--apply] [--library DIR]
        For every row of input/genre-edits.tsv, copy the named song file
        (only the .txt, no audio, video or cover) from the library into
        input/genres/<folder>/ (utilities/copy_genre_songs.py). A copy
        already there is replaced by the library's current file. Rows that
        name no song file (language.txt and the other files the stack
        writes next to a song) or a file not in the library are listed and
        left out. Without --apply only lists.

    up
        Start the batch container detached: it runs every pending job and
        exits when done (the service's default command, orchestrator.py
        run). Watch it with progress -w; it survives closing the terminal.

    run [--detach] [SELECTION] [--no-usdb]
        Run the pending jobs of the selection, plus the failed ones with
        attempts left (MAX_ATTEMPTS). In the foreground by default, tied to
        the terminal; --detach starts it in the background as container
        factory-run (an exited factory-run from before is removed first).
        --no-usdb: developer override, generate every new song from
        scratch even when USDB has it.

    genres [SELECTION]
        The genre edits: run --only genres. Each job writes the edit's
        genres, exactly as given (order kept, duplicates dropped), into the
        #GENRE of its song file from input/genres/ and puts the file into
        output/genres/<folder>/. Byte order mark, line endings and the
        encoding (UTF-8 or cp1252) stay; the input copy is not changed. A
        row whose song file is not in input/genres/ yet waits (listed as
        skipped, "pull-genres copies it"); a done edit runs again when the
        export later wants other genres for that song. Seconds per song, no
        GPU.

    list [SELECTION]
        The planned jobs and their status (registers new jobs in the state;
        runs nothing).

    report [SELECTION]
        Write output/report.md again and print it.

    progress [-w | --watch]
        The progress table; -w redraws it every 10 s. Read from the running
        batch when one runs (docker compose exec), else from a one-off
        container.

    reset [--all | --done] [SELECTION]
        Put the failed, running and needs-review jobs of the selection back
        to pending; with --done (= --all) the finished ones too. Output
        folders are never touched.

    reset --prune [SELECTION]
        Remove stored jobs that the song list, input/repairs/ and
        input/genre-edits.tsv no longer define (state backed up to
        state/backups/ first). Not combinable with --done/--all.

    run-one URL | FILE
        Create one new song from a YouTube link or a file in input/videos/,
        outside the queue (no state).

    repair-one DIR
        Repair one song folder (a path inside the container, e.g.
        /data/input/repairs/<folder>), outside the queue.

    logs
        Follow the batch container's output (docker compose logs -f).

    help
        The command summary.

SELECTION
    --only CATEGORY[,CATEGORY...]
        generated   new songs USDB does not have (created from scratch)
        usdb        new songs USDB has (notes from USDB)
        repairs     songs repaired from input/repairs/
        genres      genre edits from input/genre-edits.tsv
        Also --only=X; --only-new = generated,usdb; --only-repairs = repairs.
        Without --only every job is selected.
    --match TEXT
        Only jobs whose label contains TEXT (any case): "Band - Title" for a
        new song, the folder name for a repair or a genre edit.
    Accepted by run, genres, list, report and reset.

JOBS
    kind     id                          input                         output
    new      new|<url or file>           a song-requests.csv row       output/new/<Artist - Title>/
    repair   repair|<folder>             input/repairs/<folder>/       output/repaired/<repair>/<folder>/
    genre    genre|<folder>/<song file>  a genre-edits.tsv row +       output/genres/<folder>/<song file>
                                         input/genres/<folder>/
    <repair> is the repair done: gap, sync, recreated, lyrics, media, cover.
    A failed job's partial output goes to output/failed/<new|repair>/.

INTERACTIVE COMMANDS
    In the batch container (docker compose attach <service>, from stack/):
        s, status   the progress table
        skip        abort the current song, mark it failed, go on
        stop, q     finish the current song, then exit
        h           help
    Detach with Ctrl-P Ctrl-Q (Ctrl-C stops the container).

SAFETY CHECKS
    Before up, run, genres, run-one and repair-one factory.sh refuses to
    start when
        - a stack batch is running (both would write state/state.json;
          list, report and reset are refused then too),
        - the library check (container library-check) is running (both
          use the GPU and the CPU-heavy aligner),
        - less than FACTORY_MIN_FREE_GB of RAM is free (MemAvailable).
    A container that ends with exit code 137 was killed - usually out of
    memory; factory.sh names it so (see dmesg and stack/logs/).

SERVICES
    ultrasinger-rocm   AMD GPU (ROCm, profile "rocm"): vocal separation and
                       alignment on the GPU, whisper on the CPU. Default.
    ultrasinger        CPU only.
    Only one of the two may run at a time - they share the state.
    Plain docker compose equivalent (from stack/) of ./factory.sh run X:
        docker compose --profile rocm run --rm ultrasinger-rocm \
            python /app/orchestrator/orchestrator.py run X

FILES
    stack/input/song-requests.csv  the want-list for new songs (dashboard export)
    stack/input/broken.csv         broken-song reports (dashboard export)
    stack/input/genre-edits.tsv    genre edits (dashboard export, no header:
                                   <folder>/<song file> TAB <genres>)
    stack/input/repairs/<folder>/  song folders to repair
    stack/input/genres/<folder>/   song files for the genre edits
    stack/input/songtexts/         lyrics files a lyrics_url names
    stack/input/videos/            local song files a song-list row names
    stack/output/new/              new songs
    stack/output/repaired/<repair>/ repaired songs
    stack/output/genres/           song files with the edited #GENRE
    stack/output/failed/           partial output of failed jobs
    stack/output/report.md         the finishing report
    stack/state/state.json         every job's status (backups/: copies)
    stack/logs/<job>.log           one log per job
    stack/models/, stack/work/     model downloads, working cache (safe to delete)
    stack/cookies/cookies.txt      YouTube login for yt-dlp
    stack/.env                     the settings below (copy .env.example)

ENVIRONMENT
    Set in stack/.env (every variable there reaches the containers) or,
    for factory.sh's own settings, in the environment of the call. Details:
    configuration.md, repairs.md ("Tuning").

    factory.sh
    LIBRARY_DIR            the song library for the pulls (--library overrides)
    FACTORY_SERVICE        ultrasinger-rocm (default) or ultrasinger
    FACTORY_MIN_FREE_GB    5; RAM that must be free before a container starts
    FACTORY_ENV, FACTORY_INPUT, FACTORY_MEMINFO
                           other stack/.env, stack/input/, /proc/meminfo
                           (tests)

    Paths inside the container (set by docker-compose.yml)
    SONGS_FILE             /data/input/song-requests.csv
    INPUT_DIR              /data/input
    REPAIRS_DIR            /data/input/repairs
    BROKEN_CSV             /data/input/broken.csv
    GENRE_EDITS            /data/input/genre-edits.tsv
    GENRES_DIR             /data/input/genres
    OUTPUT_DIR             /data/output
    NEW_SONGS_DIR          /data/output/new
    REPAIRED_DIR           /data/output/repaired
    GENRES_OUT_DIR         /data/output/genres
    FAILED_DIR             /data/output/failed
    REPORT_FILE            /data/output/report.md
    STATE_DIR, LOGS_DIR, WORK_DIR   /data/state, /data/logs, /data/work
    COOKIES_FILE           /data/cookies/cookies.txt

    Accounts and keys
    USDB_USERNAME, USDB_PASSWORD      usdb.animux.de; unset: not asked
    USDB_EU_EMAIL, USDB_EU_PASSWORD   usdb.eu; unset: not asked
    GENIUS_API_KEY                    Genius lyrics source; unset: not asked

    Jobs
    MAX_ATTEMPTS           3; attempts per job before it stays failed
    JOB_TIMEOUT_MIN        90; hard limit per song
    CREATOR_NAME           #CREATOR of generated songs; empty: USDX-Song-Factory
    CREATOR_FACTORY_SUFFIX 1; " with USDX-Song-Factory" after the name, 0: off
    LYRICS_ENABLED         1; 0: new songs keep whisper's transcription
    LYRICS_FILL            1; lines whisper heard but the lyrics lack are filled in
    ROMANIZE               1; 0: no romanization
    ROMANIZE_STYLE         scientific | readable (Russian)
    REPAIR_MODE            sync; repair mode of a folder without a broken.csv row
    REPAIR_FALLBACK        1; re-create songs whose lyrics don't fit the audio
    REPAIR_LANGUAGE        force the aligner language (e.g. de)
    ULTRASINGER_ARGS       extra arguments for every UltraSinger run
    TZ                     Europe/Berlin

    Matching and checks
    USDB_MIN_MATCH_SCORE   0.90; artist+title similarity for a USDB match
    USDB_CATALOG_TTL_HOURS 24; reuse of the animux.de catalog cache
    BROKEN_MATCH_MIN_SCORE 0.85; #ARTIST/#TITLE similarity for a broken.csv row
    GAP_MAX_SPREAD_S       0.3; agreement a re-detected #GAP needs
    LYRICS_REVIEW_MIN_HEARD        0.6; lyrics review: share of words heard
    LYRICS_REVIEW_MAX_UNHEARD_RUN  5; lyrics review: unheard lines in a row
    ENDCARD_MIN_GAP_S      8.0; silence before a spoken end-card (<= 0: off)
    ENDCARD_MAX_DUR_S      20.0; longer trailing speech stays lyrics
    MUSICBRAINZ_TIMEOUT_S  60; silent MusicBrainz connection before a retry
    LRCLIB_MAX_WAIT_S      10; wait for a busy lrclib.net

    Models and resources
    STACK_RAM_GB 8, STACK_SWAP_GB 250, STACK_CPU_CORES 16, STACK_VRAM_GB 16,
    STACK_RAM_SAFETY_MARGIN_GB 1.5, STACK_VRAM_SAFETY_MARGIN_GB 2
                           the budget the models are chosen from
    WHISPER_MODEL, DEMUCS_MODEL, WHISPER_BATCH_SIZE
                           force a choice (default: from the budget)

    Timing and pitch tuning (lyrics mode; change only for experiments)
    MAX_ALIGNED_WORD_SECONDS 4.0, LYRICS_LINE_OUTLIER_GAP_S 3.0,
    WORD_END_EXTEND_DROP_DB 6.0, LOUD_INSTRUMENTS_WORD_END_DROP_DB 9.0,
    WORD_END_EXTEND_MAX_S 3.0, WORD_START_LEAD_S 0.04,
    LYRICS_END_SKIP_PENALTY 4.0, LYRICS_FILLER_ENTER 5.0,
    LYRICS_FILLER_BETA 1.0, LYRICS_FILLER_NOTE_S 2.0,
    LYRICS_FILL_OPTIONAL_COST 2.0, LYRICS_MAX_LINE_CHARS 60,
    LYRICS_LINE_BREAK_PAUSE_S 2.5, SYNC_PRECORRECT_MIN_S 1.0,
    SYNC_PRECORRECT_DRIFT_S 0.5, PITCH_MIN_WINDOW_S 0.2,
    PITCH_READ_DELAY_S 0.1, TILDE_PENALTY 40, HARSH_MAX_DISTANCE 3,
    HARSH_UNSURE_BELOW 0.3, PITCH_FOLD_TOLERANCE 7, PITCH_SONG_TOLERANCE 18,
    PITCH_SONG_TOLERANCE_HARSH 12, REPAIR_ALIGN_PAD 6.0,
    REPAIR_MIN_WORD_SCORE 0.6, REPAIR_MAX_LOCAL_DEVIATION 3.0

EXIT STATUS
    0      done
    1      refused by a safety check, a missing setting or file
           (factory.sh), an unknown orchestrator command
    2      an unknown factory.sh command, a wrong selection option
    137    the container was killed, usually out of memory
    other  the container's own exit code

EXAMPLES
    Repair the songs of a dashboard report and copy them back:
        ./factory.sh pull-repairs --apply
        ./factory.sh run --detach --only repairs
        ./factory.sh progress -w
    Apply the dashboard's genre edits:
        ./factory.sh pull-genres --apply
        ./factory.sh genres
        then copy stack/output/genres/<folder>/<song file> into the library
    Redo one song:
        ./factory.sh reset --done --match "Rauta"
        ./factory.sh run --match "Rauta"
    The CPU service for one call:
        FACTORY_SERVICE=ultrasinger ./factory.sh list

SEE ALSO
    running.md, new-songs.md, repairs.md, genre-edits.md, configuration.md,
    orchestrator-manual.md, ../../utilities/README.md
```
