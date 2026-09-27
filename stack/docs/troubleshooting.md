# Troubleshooting

Commands are run from inside `stack/`; `orchestrator.py` stands for
`docker compose run --rm ultrasinger python /app/orchestrator/orchestrator.py`.

* **A song failed** - check `logs/<song>.log` and `output/report.md`.
  `orchestrator.py reset` re-queues failed jobs, `docker compose up -d`
  retries them. Partial output is in `output/failed/`, never mixed into your
  songs.
* **"Sign in to confirm you're not a bot" / age-restricted video** - YouTube
  wants a logged-in session: [youtube-cookies.md](youtube-cookies.md).
* **`Permission denied` on `models/`, `state/`, `output/` ...** - the
  container runs as uid 1000. If Docker created the folders (as root) because
  they did not exist yet, give them back to your user:
  `sudo chown -R 1000:1000 input output state logs models work cookies`.
  Creating them yourself before the first run avoids this (see the quickstart
  in the main README).
* **A setting in `.env` has no effect** - only the variables listed in
  `docker-compose.yml` are passed into the container; see
  [configuration.md](configuration.md).
* **Models are downloaded on every run** - `models/` must be writable by the
  container (uid 1000, see above).
* **`local input file not found: ...`** - a `song-requests.csv` url that is not
  an `https://` link is treated as a file path under `input/`; check the
  spelling and that the file is there.
* **A song's lyrics look mis-heard** - check `output/report.md`: a song whose
  lyrics came from transcription (flagged with a warning mark) had no online
  lyrics source. Add a `lyrics_url` to its row in the song list and reset it,
  or repair it with a `lyrics.txt` ([repairs.md](repairs.md#trusted-lyrics-lyrics-mode)).
  A `GENIUS_API_KEY` may help for less common songs.
* **Lyrics contain a line like "Chorus"** - a section marker the lyrics
  source wrote in an unusual form slipped through; fix the text via a
  `lyrics.txt` and repair the song.
* **A song ends with a stray spoken line** - the end-card filter
  (`ENDCARD_MIN_GAP_S`/`ENDCARD_MAX_DUR_S`) is a heuristic: a long spoken outro
  or a very short one right after the last sung line is kept. Tune the
  thresholds or fix the txt by hand.
* **Wrong language detected for a repair** - set `#LANGUAGE` in the song txt,
  or `REPAIR_LANGUAGE` ([repairs.md](repairs.md#tuning-rarely-needed)).
* **A `broken.csv` repair failed with "no existing file to link and no usdb
  sync-meta source"** - category `video`/`audio`, and neither an unambiguous
  local file nor a `*.usdb` file was found; add the file (or a `.usdb` file),
  or change the category.
* **"multiple candidate files found"** - two or more unrelated media files
  sit in the folder of a `video`/`audio` repair; rename the one to keep so its
  name matches the `.txt`, or remove the other.
* **A job shows up as `needs_review`** - its `broken.csv` category is blank,
  `other` or unknown, or (for `video`/`audio`) the reported defect was not
  there (the file already existed). Fix the report or the folder, then
  `orchestrator.py reset`.
* **Pending jobs that never run** - a song removed from the song list stays in
  `state/state.json`; the progress view shows it on the "Not counted" line.
  `orchestrator.py reset --prune` removes it.
* **Repair quality** - if a line could not be aligned at all, it keeps the
  original or estimated timing (visible in the log).
* **Disk usage** - `work/` (working cache) can be deleted any time. A song
  folder is usually 10-100 MB, mostly the video.
