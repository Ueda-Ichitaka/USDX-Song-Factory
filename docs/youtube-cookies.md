# YouTube cookies

For "Sign in to confirm you're not a bot" errors and age-restricted videos.

yt-dlp occasionally hits YouTube bot protection, and age-restricted videos
always need a logged-in session. Export your browser's YouTube cookies once,
from the repository root on the host
(not inside the container - the container has no browser and no login),
then verify them through the docker image (below) rather than on the host -
see why under "Verify the cookies" first if you're tempted to skip it.

```bash
# close the browser first - it locks its cookie DB while running, which
# makes yt-dlp fail with "database is locked"
yt-dlp --cookies-from-browser firefox --cookies stack/cookies/cookies.txt
```

- `--cookies-from-browser` also accepts `chrome`, `brave`, `edge`, ... - and
  a specific profile if you have more than one: `firefox:default-release`,
  `chrome:Profile 2`.
- No video URL is needed for the export itself - yt-dlp still prints
  `error: You must provide at least one URL` and exits non-zero, but only
  AFTER writing the cookie file, so that message is expected and harmless
  here (verify the file, not the exit code).
- The output file must be in Mozilla/Netscape format (first line `# Netscape
  HTTP Cookie File`) - `--cookies-from-browser` + `--cookies <file>` writes
  it in that format automatically, don't hand-edit it.
- Cookies expire - if bot-check errors come back after a while, just re-run
  the command above.

The orchestrator automatically passes `--cookiefile` to UltraSinger when
`cookies/cookies.txt` exists - no further config needed once the file is in
place.

## If `yt-dlp` isn't installed on the host

The command above needs a real `yt-dlp` on the host - the one baked into
the docker image can't reach a browser's cookie database or its login
keyring. Give it a throwaway environment instead of installing anything
system-wide:

```bash
# uv (https://docs.astral.sh/uv/): ephemeral venv, removed when you're done
uv venv /tmp/ytdlp-cookies-env
uv pip install --python /tmp/ytdlp-cookies-env/bin/python yt-dlp secretstorage
/tmp/ytdlp-cookies-env/bin/yt-dlp --cookies-from-browser firefox \
  --cookies stack/cookies/cookies.txt
rm -rf /tmp/ytdlp-cookies-env

# or plain pip, if you don't mind it staying installed
pip install --user yt-dlp secretstorage
```

`secretstorage` is only needed on Linux, and only when your cookies are
encrypted with the desktop's keyring/wallet (see below) - Firefox doesn't
need it.

## Chromium-based browsers (Chrome/Brave/Edge/...) installed as a Flatpak

`--cookies-from-browser` looks for the browser's *native* Linux install
path (e.g. `~/.config/BraveSoftware/Brave-Browser`). A Flatpak install
lives elsewhere and needs its profile folder given explicitly as
`BROWSER:PATH`:

```bash
# find it first - the "Cookies" file lives inside a profile folder such as
# "Default" or "Profile 1"
find ~/.var/app/*/config -mindepth 1 -maxdepth 1 -iname '*rowser*' 2>/dev/null
find ~/.var/app/com.brave.Browser/config -iname Cookies

# then pass that profile folder (the one CONTAINING "Cookies", not the
# file itself) after the browser name
yt-dlp --cookies-from-browser \
  "brave:$HOME/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default" \
  --cookies stack/cookies/cookies.txt
```

On Linux, Chromium-based browsers encrypt cookie values with a key stored
in the desktop's keyring (GNOME Keyring, KDE's `ksecretd`/KWallet, ...) via
the `org.freedesktop.secrets` D-Bus service - `secretstorage` (installed
above) is yt-dlp's client for that. This only works from a normal desktop
session (a real `DBUS_SESSION_BUS_ADDRESS`, so a plain SSH shell won't have
it) and will prompt you to unlock the keyring/wallet if it's locked. If
extraction reports 0 cookies or decryption failures, the keyring is most
likely locked or unreachable.

Firefox stores cookies unencrypted in its own SQLite database (no keyring
involved), which is why it needs no keyring/`secretstorage` step.

## Verify the cookies

Do this through the docker image, not a bare host `yt-dlp` - resolving a
real YouTube format needs a JS runtime for YouTube's signature challenge
(the image bundles `deno` for exactly this; a plain `pip`/`uv`-installed
`yt-dlp` on the host does not), so a host-only check can fail with an
unrelated `Signature solving failed` / `age-restricted` error even when the
cookies themselves are perfectly good:

```bash
cd stack
docker compose run --rm ultrasinger yt-dlp --cookies /data/cookies/cookies.txt \
  --skip-download --print title "https://www.youtube.com/watch?v=<the-blocked-video-id>"
```

Printing the real title (instead of a bot-check/age error) confirms the
cookies work - use the actual previously-blocked video's URL here to
confirm it specifically before you retry the batch.
