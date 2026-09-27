# `orchestrator.py` manual

Man-page style reference of the batch orchestrator. Inside the container the
program is `python /app/orchestrator/orchestrator.py`; from the host (inside
`stack/`) prefix it with `docker compose run --rm ultrasinger` (GPU service:
`ultrasinger-rocm`). A task-oriented introduction is in
[running.md](running.md).

```text
ORCHESTRATOR.PY(1)              UltraStar Song Factory              ORCHESTRATOR.PY(1)

NAME
    orchestrator.py - create new UltraStar songs and repair existing ones in
    batch, with resumable per-song state

SYNOPSIS
    orchestrator.py [run] [SELECTION] [--no-usdb]
    orchestrator.py list|report [SELECTION]
    orchestrator.py progress [-w | --watch]
    orchestrator.py reset [--all | --done] [SELECTION]
    orchestrator.py reset --prune [SELECTION]
    orchestrator.py run-one URL
    orchestrator.py repair-one DIR

    SELECTION = [--only CATEGORY[,CATEGORY...]] [--match TEXT]

DESCRIPTION
    Reads the want-list (input/song-requests.csv) and the song folders in
    input/, plans one job per song, runs the pending ones one after another
    and records every result in state/state.json. Finished jobs are skipped
    on the next run, so a run can be stopped and resumed at any time.

    A "new" song is created in this order: if USDB has the song, its notes
    are pulled from USDB, the fitting video and audio are added and #GAP is
    re-detected; otherwise the song is generated from scratch (vocal
    separation, transcription, and online lyrics force-aligned to the audio
    when a lyrics source is found). A repair job fixes a song folder from
    input/ (#GAP only, resync, or trusted lyrics - see repairs.md).

COMMANDS
    run                 Process all pending jobs. This is the default when no
                        command is given (and what `docker compose up` runs).
    list                Show the planned jobs and their status; nothing runs.
    report              Print and write the finishing report (output/report.md).
    progress [-w]       Print the progress overview once; with -w/--watch keep
                        redrawing it every 10 seconds.
    reset               Put failed, running and needs_review jobs back to
                        pending so the next run retries them.
    reset --done        Additionally put FINISHED jobs back to pending.
    reset --all         Same as --done.
    reset --prune       Remove stored jobs that are no longer in the song list
                        or the input folder ("orphans"). The state file is
                        backed up to state/backups/ first, output folders are
                        never touched, running jobs are kept. SELECTION
                        narrows it; cannot be combined with --done/--all.
    run-one URL         Create one song from a YouTube URL, bypassing the queue
                        and the state file.
    repair-one DIR      Repair one song folder, bypassing the queue and the
                        state file.

SELECTION OPTIONS  (run, list, report, reset)
    --only CATEGORY[,CATEGORY...]
                        Restrict to job categories (also --only=VALUE):
                          generated  new songs USDB does not have; created
                                     from scratch
                          usdb       new songs USDB has; pulled from USDB
                          repairs    songs repaired from input/
                        Whether a new song is "generated" or "usdb" is only
                        known from a USDB lookup, so before a run with
                        `generated` or `usdb` every pending new song is
                        checked against USDB and the ones that do not fit are
                        left pending (without USDB credentials every song
                        counts as "not on USDB"). For reset, the category
                        comes from the recorded result (lyrics source
                        "usdb:...").
    --only-new          Older alias for --only generated,usdb.
    --only-repairs      Older alias for --only repairs. Both work with reset,
                        e.g. `reset --done --only-new` redoes every new song,
                        `reset --done --only-repairs` every repair.
    --match TEXT        Only jobs whose label contains TEXT (case-insensitive).
                        Combines with --only.

RUN OPTIONS
    --no-usdb           Developer override: never pull from USDB, generate
                        every new song from scratch even if USDB has it (to
                        compare alignment quality, for example). Off by
                        default - the normal policy always pulls a song that
                        USDB has. Cannot be combined with --only usdb.

INTERACTIVE COMMANDS  (attached to a running `docker compose up`)
    s, status           Print the progress overview.
    skip                Abort the current song (marked failed), go on.
    stop, q             Finish the current song, then exit.
    h                   Help.
    See running.md, "Interactive commands".

ENVIRONMENT
    SONGS_FILE, BROKEN_CSV, INPUT_DIR, OUTPUT_DIR, NEW_SONGS_DIR, STATE_DIR,
    LOGS_DIR, WORK_DIR, MAX_ATTEMPTS, JOB_TIMEOUT_MIN, LYRICS_ENABLED,
    GENIUS_API_KEY, USDB_USERNAME, USDB_PASSWORD, USDB_EU_EMAIL,
    USDB_EU_PASSWORD, ROMANIZE, WHISPER_MODEL, DEMUCS_MODEL.
    Meaning and defaults: see configuration.md.

FILES
    input/song-requests.csv   want-list of new songs
    input/broken.csv          reports describing what is wrong with a repair
    input/<song folder>/      songs to repair
    output/new/               created songs
    output/repaired/          repaired songs
    output/failed/<kind>/     partial output of failed jobs
    output/report.md          finishing report
    state/state.json          per-job status (can be hand-edited)
    logs/<job>.log            full verbose output of each job
    cookies/cookies.txt       optional YouTube cookies (age-restricted videos)

EXIT STATUS
    0     the command completed (run also exits 0 after a deliberate stop)
    1     unknown command; run-one / repair-one failed or got a bad argument
    2     invalid SELECTION option value

EXAMPLES
    Run everything that is pending:
        orchestrator.py run

    Only the songs that must be created from scratch:
        orchestrator.py run --only generated

    Only songs available on USDB, plus all repairs:
        orchestrator.py run --only usdb,repairs

    Generate a song from scratch although USDB has it (comparison):
        orchestrator.py reset --done --match "Daddy Cool"
        orchestrator.py run --match "Daddy Cool" --no-usdb

    Redo every finished generated song, then run just those:
        orchestrator.py reset --done --only generated
        orchestrator.py run --only generated

    Retry only the failed jobs:
        orchestrator.py reset
        orchestrator.py run

    Preview what a selection would touch:
        orchestrator.py list --only repairs --match "Nightwish"

    Redo all new songs / all repairs:
        orchestrator.py reset --done --only-new
        orchestrator.py reset --done --only-repairs

    Drop jobs whose song was removed from the list or input folder:
        orchestrator.py reset --prune

SEE ALSO
    running.md, new-songs.md ("Choosing which jobs run"), repairs.md,
    song-sources.md, configuration.md
```
