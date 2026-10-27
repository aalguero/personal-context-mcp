# Voice assistant — architecture and state

Started 2026-10-08. Separate component from futile-pa; it joins at stage 4.
Backlog items for the task backend stay in `architecture/backlog.md`.

**Last updated: 2026-10-14 (afternoon). Stage 1 (speech-to-text) is complete. Stage 2a is
complete: LVA runs in Docker on the Pi 5, HA has adopted it, the stock "okay computer" at
sensitivity 0.90 passed a one-hour soak with zero false wakes, and a cold reboot with no
login brought the satellite back and executed a spoken command with a spoken reply. Wake word decision (Andre, 2026-10-14): stay with the stock "okay computer"; custom
word deferred. Next: stage 3 (TTS is already speaking; routing, barge-in and
vocabulary hints remain). Resume at "Pending steps".**

## Decisions made

- **Host**: `vocalassistant`, a Raspberry Pi 5 (7.9 GiB RAM), full 64-bit Raspberry Pi
  OS (Trixie, Python 3.13), **run headless** (services under systemd, no desktop dependency).
- **Pattern**: Home Assistant's Assist pipeline. The Pi is the audio edge; HA is the
  router. No custom router is built on the Pi.
- **Pi 5 runs**: **Linux Voice Assistant (LVA)** for microphone, speaker and wake word
  (decided 2026-10-11), and wyoming-faster-whisper for speech-to-text.
  LVA talks to HA over the **ESPHome** protocol (HA connects to the Pi on port 6053);
  Whisper stays on **Wyoming** (port 10300).
- **Why LVA and not `wyoming-satellite`**: wyoming-satellite is archived (frozen, 2023-era
  pins that need source builds on Python 3.13). LVA is maintained, runs the wake word on
  the device, and is the install the maintainers recommend as Docker. Costs: it is
  labelled experimental, and upstream has no XVF3800 documentation, so the array is
  treated as a generic USB audio device (works in principle, unproven until stage 2 checks run).
- **Home Assistant**: HA OS on a **Pi 4** (`homeassistant`). Too small to host Whisper,
  which is why STT runs on the Pi 5.
- **Routing**: local-first. HA intent matching handles home commands; anything
  unmatched goes to a fallback conversation agent that calls futileMCP (stage 4).
- **Network**: LAN primary, Tailscale as backup. Whether the Pi 5 is on the tailnet is unconfirmed (see Pending steps).
- **Addresses**: both Pis have **DHCP reservations on the router** (2026-10-10), each
  verified to survive a full reboot. HA is given the Pi 5's **IP**, not
  `vocalassistant.local`. **The Pi 5 is on Wi-Fi (`wlan0`, 192.168.86.251), by choice:**
  there is no wired option near the microphone, which is in the main living area.
  LVA derives its device identity from the interface MAC, so `NETWORK_INTERFACE=wlan0`
  is pinned to stop a later cable from changing it. The Pi 5 also has a `100.x`
  address in the preflight output, which looks like Tailscale (not confirmed).
- **Microphone: Seeed ReSpeaker XVF3800, USB** (bought 2026-10-11). Hardware echo
  cancellation, beamforming, noise suppression and AGC. USB rather than an I2S HAT
  because the PiTower Gen2 case uses SPI1 on GPIO 18 to 21, which conflicts with I2S mic HATs.
  **Speaker plugged into the array** (2026-10-11): hardware echo cancellation only
  cancels audio played through the array itself, so the speaker must stay on the array.
- **Wake word**: custom, on the Pi (so the mic does not stream to HA continuously).
  Candidates given 2026-10-11: **"okay computer"**, **"upon my command"**,
  **"futile assistant"**. LVA ships a stock `okay_computer` model (microWakeWord, cutoff
  0.97), so stage 2 is tested with that before any training. Reasoning for the custom
  one: "futile assistant" has the longest, most distinctive phoneme sequence (fewest false
  accepts, and matches the futileMCP name); "upon my command" is short on stressed
  syllables; "okay computer" is likely to fire on TV and video calls. Final pick after the soak test.
- **STT model: `base-int8` with `--compute-type int8`** (decided 2026-10-08 on
  measurement). About 1.8 s for a 1.5 s command, versus about 5.3 s for `small-int8`.
- **No accelerator HAT for now.** Hailo options exist but need a different STT backend
  the Wyoming server does not ship, and CPU latency already meets the target.

### Stage 2 configuration decisions (from LVA v1.1.15 source)

| Decision | Reason |
|---|---|
| `AUDIO_INPUT_CHANNELS=1` | LVA treats channel 1 as an echo reference for server-side AEC and advertises multi-channel to HA. The XVF3800's channel 1 is a second beam, not a reference |
| Leave `MIC_AUTO_GAIN` and `MIC_NOISE_SUPPRESSION` unset | The array does both in hardware; software WebRTC on top is double processing, and setting them in `.env` locks the HA switches |
| No software (PipeWire) echo cancel | Hardware AEC only. A second AEC stage on top would be double processing |
| Image tag pinned to `1.1.15`, never `latest` | The `av` 19 incident was an unpinned dependency |
| `WAKE_MODEL` is only the first-run default | After the first run `preferences.json` wins and the HA "Wake Word 1" select edits it |
| Debian's `docker.io` 26.1 and `docker-compose` 2.26 packages, run with `sudo docker` | Already-trusted apt sources, patched by Debian, no new signing key. The `docker` group is root-equivalent, so the user is deliberately not added to it. Confirmed working 2026-10-11 |
| Docker over bare metal | CI-built image freezes the dependency set and avoids compiling `pymicro-features` on the Pi. Cost: Docker daemon plus container-to-PipeWire socket plumbing, the likeliest failure point. Bare metal remains the fallback |

## How Home Assistant is reached (set up 2026-10-10)

The phone refused plain-HTTP Home Assistant, and the browser microphone needs a
secure origin. Solution: Tailscale Serve, which terminates TLS with a Let's Encrypt
certificate for the tailnet name. **Tailnet-only, not Funnel** — HA is not exposed to
the public internet.

| | |
|---|---|
| External URL | `https://homeassistant.cormorant-barb.ts.net` (no port). Needs Tailscale running on the phone. Verified on a laptop and on the phone over cellular |
| Tailscale app option | `share_homeassistant: serve` (allowed: `disabled`, `serve`, `funnel`), `share_on_port` 443 |
| HA reverse proxy | Settings, System, Network, HTTP server: **Trust X-Forwarded-For on**, trusted proxy **`127.0.0.1`**, SSL/TLS **off** (HA serves plain HTTP behind the proxy). No 400 error seen, so the value is right |
| Tailscale admin | MagicDNS on, Enable HTTPS on. No manual certificate step; Serve fetches and renews it |
| Companion app | External URL set to the address above. **Internal URL / home Wi-Fi name not yet configured** (optional — see below) |

**Optional, left undone:** the Companion app internal URL (`http://<Pi 4 reserved
IP>:8123`). Since Companion app 2025.11 an unencrypted URL needs a "connection
security level" choice: the safe option only allows it on a named home Wi-Fi, and
reading the Wi-Fi name needs Android location permission **Allow all the time +
Precise**. The external HTTPS URL works at home too, so this is a convenience only.

## Stages

1. **STT on the Pi 5 + HA integration** — **complete as of 2026-10-10.**
2. **Microphone, speaker, wake word, satellite (LVA)** — designed 2026-10-11, install
   files written (`docs/STAGE2.md`, 10 checks). **2a complete 2026-10-14** (checks 0 to 10 below).
   - 2a: audio plumbing, LVA container, stock `okay_computer`, HA adoption, spoken command end to end.
   - 2b: train and install the custom wake word, repeat the wake checks for it.
3. TTS (spoken replies), and Whisper vocabulary hints from HA entity names.
4. Fallback agent into futileMCP: HA conversation agent calling `pa_*` tools.

### Stage 1 check status (see `docs/STAGE1.md` in `vocal-assistant.tar.gz`)

| Check | Status |
|---|---|
| 1 Service up and listening | Done |
| 2 Identifies itself (`--describe`) | Done |
| 3 Transcribes real speech, latency measured | Done. 5.3 s small, 3.2 s base (default compute type), 1.8 s base with `--compute-type int8` |
| 4 Config controls the service | Partial. Switching `WHISPER_MODEL` in `whisper.env` changed the model that ran, so the file is read. The deliberate bad-value failure test was not run |
| 5 Survives a reboot | Done, and again after the address reservation reboots |
| 6 Home Assistant sees it | **Done 2026-10-10** |
| 7 End to end from the HA Android app | **Done 2026-10-10** |

### Stage 2 check status (see `docs/STAGE2.md`)

| Check | Status |
|---|---|
| Mic check (done before the numbered list) | **Done 2026-10-11.** XVF3800 detected over USB; both channels transcribed "Turn off the kitchen lights." correctly, 6 s recordings, 1.6 to 1.8 s after last audio, via ALSA directly. Channel choice not critical |
| 0 Preflight on the Pi | **Done 2026-10-11** |
| 1 Linger / audio survives no login | **Done.** Was `Linger=no` (found by the preflight); fixed with `sudo loginctl enable-linger dremon`, now `Linger=yes`. Real proof is check 10 |
| 2 Mic through the sound server, transcribed | **Done.** PulseAudio-on-PipeWire 1.4.2; array is the default source and sink at 16 kHz; mono capture through the server transcribed correctly, 1.7 s |
| 3 Speaker plays through the array | **Done.** Heard from the array's speaker |
| 4 Docker present, image tag pulls | **Done.** `1.1.15` pulled for arm64 |
| 5 Satellite up, opens the array | **Done.** Container reaches the sound server; source-outputs shows a `float32le 1ch 16000Hz` stream on the array source, which went SUSPENDED to RUNNING |
| 6 HA adopts it | **Done** |
| 7 Spoken command end to end | **Done.** "okay computer" then a command executes in HA. Too picky about pronunciation at the stock cutoff 0.97 |
| 8 Config controls behaviour | **Done by log evidence 2026-10-14.** Wake Word 1 Sensitivity set to 0.90 in HA; `preferences.json` shows `wake_word_1_sensitivity 0.8999999761581421` and activation log lines show threshold 0.900 (was 0.970). Volume also persisted and takes effect. The Wake Word 1 select with a different model is untested until stage 2b |
| 9 False-accept soak, one hour | **Done 2026-10-14, stock `okay_computer` at 0.90:** zero `Detected wake word` lines in 14:32 to 15:32 EDT with TV or music playing (the room was not confirmed to be noisy the whole hour, so the 95% upper bound of about 3 per hour holds only if it was). Positive controls: earlier and later deliberate triggers (0.97 to 0.98 and 0.995 to 0.996) appear in the same grep. Stop-word model fired at 0.50 to 0.77 three times in the window with no wake. Repeat after any sensitivity change or new wake word |
| 10 Survives reboot with no login | **Done 2026-10-14.** Boot 16:16:44; container `Up 2 minutes (healthy)` with nobody logged in; `Linger=yes`; sink 85% (-4.24 dB), `PCM,0` 56/60 (-4 dB) persisted; "okay computer, turn off the kitchen lights" was transcribed, executed, and HA's reply "Turned off the light" played back through the Pi |

## Pending steps for Andre

1. **Stage 2b: custom wake word: DEFERRED.** Andre decided 2026-10-14 to keep stock "okay computer"
   at 0.90: it works well with no false positives. Revisit only if that changes. Lead
   candidate if so: "futile assistant". Research the current microWakeWord training workflow,
   train, install `.tflite` plus JSON manifest into `stage2/wakewords-custom/`, then repeat
   hit-rate, one-hour soak and reboot checks for it.
2. **Stage 3:** TTS already speaks (the HA pipeline's TTS reply played on the Pi 2026-10-14; the
   choice of engine is still not recorded). Remaining: confirm the reply is audible at 85%,
   confirm it plays through the array so hardware AEC sees it, test barge-in, and Whisper
   vocabulary hints from HA entity names (`--hass-token`, mode 600 file).
3. **Broader STT accuracy sample.** Record phrases on a phone using real HA room and
   device names, including awkward ones, convert with
   `sox in.wav -r 16000 -c 1 -b 16 /tmp/test.wav`, run through `transcribe_wav.py`.
   Earlier measurements used synthetic speech, the easy case.
4. **Optional:** confirm whether the Pi 5 is on the tailnet (a `100.x` address appeared in
   the preflight); Companion app internal URL.

### Settled in stage 2a (2026-10-14), for reference

- **Volume 85%** on the array sink (was 40%, -23.88 dB, "barely audible"). PipeWire percentages
  are cubic: amplitude = fraction^3, so 85% is 0.614 or -4.24 dB. The array's hardware mixer
  (`PCM,0`, 0 to 60 steps of 1 dB) tracks the sink, so there is one volume stage.
- **Output pinned:** `AUDIO_OUTPUT_DEVICE="pulse/alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993701262801356-00.analog-stereo"`.
  An HDMI sink appeared and could become the default; with the pin, chimes stayed on the array
  while the default pointed at HDMI (tested).
- **Sensitivity 0.90** (HA number control under the device's Configuration section, not in
  the Controls card).
- **Debug logging is OFF** again (`ENABLE_DEBUG` removed from `.env`, container recreated).
  Turn it on only for a test: wake detections log at debug level only, and Docker's default
  json-file logs do not rotate.
- `docker compose` commands must run in `~/developer/vocal-assistant/stage2/`;
  `sudo docker logs linux-voice-assistant 2>&1` works from anywhere.

Task 108 (`apt install age rclone rsync`) was closed on 2026-10-10 at Andre's
instruction after he verified the install paths. Still open, unrelated to this
component: printed copy of the age key (task 111), LUKS check (task 112), B2 bucket
(task 103), case code into git (task 114, due 2026-10-11). See `architecture/deployment-status.md`.

Voice-assistant steps are deliberately not in the task system (Andre, 2026-10-11: the XVF3800 is a plain USB board; do not add those as tasks for now).

## Decisions waiting on information

1. Final wake phrase: **stock "okay computer" for now** (2026-10-14); custom phrase deferred.
2. Whether `base-int8` stays accurate on a wider set of voices and HA entity names.
   If not, the fix is Whisper vocabulary hints from HA (`--hass-token`, long-lived
   token in a mode 600 file), not a bigger model.
3. Which TTS engine the HA pipeline uses (replies already play; the engine was not recorded).
4. HA mechanism for the stage 4 fallback agent: MCP client integration vs a custom
   conversation agent. Verify against the installed HA version (2026.8 or later).
5. Stage 3: whether to route TTS and all playback to the array so its hardware AEC
   sees it (expected yes), and how to test barge-in.

## Corrections that came back from the machine

1. **`av` 19.x breaks faster-whisper 1.2.1.** `faster-whisper` declares `av>=11` with
   no upper bound, so pip installed `av` 19.0.1, which rejects the `metadata_errors`
   argument. Symptom: service healthy, model downloaded, then the first transcription
   drops the connection. Fix: pin `av>=15,<19`. The installer runs a decode self-test.
2. **A fix to a package is not live until the service restarts.** The old process kept
   the old module in memory and failed identically. Diagnosed from `ActiveEnterTimestamp`
   predating the `pip install`.
3. **Check 1 ("service is up") proves nothing about transcription.** The model loads
   lazily on the first audio. Only a real transcription proves the path.
4. **The server's default `compute_type` made `base-int8` about 1.4 s slower**
   (3.2 s vs 1.8 s). Found by comparing the model run directly (`bench_stt.py`) against
   the server. Cause not investigated; the fix is measured and the unit passes
   `--compute-type int8`.
5. The hidden `.venv` and install location (`~/vocal-assistant`) cost a few minutes.
6. **The Tailscale app dropped the `proxy`, `funnel` and `proxy_and_funnel_port`
   options** (now `share_homeassistant`, `share_on_port`). HA silently strips unknown
   add-on option keys, so the added line "reverted on save". **Rule: check the current
   docs and the add-on's schema before giving option names; a setting that reverts on
   save is an unknown key, not a glitch.**
7. **HA 2026.8 and later import the `http:` YAML block into the UI and then ignore it.**
   Proxy settings live under Settings, System, Network, HTTP server. The trusted proxy
   is `127.0.0.1`, not `172.30.33.0/24`.
8. **An earlier outage after enabling the old `proxy` option is unexplained.** Rule that
   came out of it: make restarts while on the LAN, and keep the LAN address as the fallback path.
9. **`.local` names do not resolve reliably on Android.** Use the reserved IP.
10. **A DHCP reservation takes effect only when the device renews its lease.** A full
    host reboot or power cycle applies it; "Restart Home Assistant" does not.
11. **The Hugging Face "unauthenticated requests" warning in the Whisper journal is
    harmless.** Do not set an `HF_TOKEN`.
12. **LVA's channel semantics differ from what the default suggests.** Reading the
    source showed channel 1 is treated as an echo reference, so the default
    `AUDIO_INPUT_CHANNELS=2` would mislabel the XVF3800's second beam. Found before
    anything ran, by reading `satellite.py` and `__main__.py`, not by a failure.
13. **`WAKE_MODEL` does not control the wake word after first start.** `preferences.json`
    wins. A "change the env, restart" test would therefore pass or fail for the wrong
    reason; check 8 changes the HA select instead.
14. **The cloud workspace cannot reach `ghcr.io` or `download.docker.com`** (egress
    policy). The image tag `1.1.15` and Docker's Trixie packages are unverified until
    run on the Pi. The `.env` to flags mapping was verified by running LVA's real
    entrypoint with the app stubbed.

15. **The preflight caught `Linger=no`.** Audio worked only because a login session was
    active; with nobody logged in after a reboot the sound server would not have started
    and the satellite would have had no audio. Fixed before first start.
16. **The sound server's command-line tool (`pactl`) was not installed**, though the
    server itself was running. Check the tool, not just the socket.
17. **Names differ between tools.** `pactl` shows node names; LVA lists inputs by
    description and outputs in mpv's `pulse/<node>` form. The device-name lookup was
    skipped because the array was the only device and the default for both directions.

18. **A number read from the wrong control.** "Sensitivity is at 85%" was actually the volume. Caught
    because `preferences.json` still held `wake_word_1_sensitivity: null` and the log threshold
    was 0.970. Rule: confirm a setting from the file or log that stores it, not from a description.
19. **An empty grep needs a positive control.** The first soak query returned nothing; a
    broader grep over the same logs showed earlier and later wake events, proving the pattern
    and the logging worked and the quiet hour was real.
20. **`docker compose` fails with "no configuration file provided" outside `stage2/`.** Use
    `sudo docker logs ... 2>&1` for logs from any directory.
21. **A config edit is not live until recreate.** The first `ENABLE_DEBUG` `sed` edit did not
    take (no line in `docker compose config`); verify with `docker compose config`, not the file.
22. **Reboot check needs the boot time.** Volume values alone look the same without a reboot;
    `uptime -s` plus container uptime plus a spoken command proved it.

## Measured on the Pi 5 (1.5 s command, 4 threads)

| Setup | Time |
|---|---|
| small-int8 | about 5.3 s |
| base-int8, server default compute type | about 3.2 s |
| base-int8, `--compute-type int8` | about 1.8 s |
| base-int8 via XVF3800 (6 s recording, either channel) | about 1.6 to 1.8 s after last audio |

Hardware is healthy during the runs: 2.4 GHz, throttle flags `0x0`, 37 to 49 C. The
30 s Whisper window means short commands cost close to long ones.

## Future features

- **Optimize the Pi 5 OS** (Lite or purpose-built minimal image), decided on measured numbers.
- Whisper vocabulary biasing from HA entity and area names (`--hass-token`).
- If 1.8 s proves too slow in daily use: `tiny-int8`, a streaming engine, or a Hailo accelerator.
- LVA peripheral WebSocket API (port 6055) for buttons or LEDs on the array, if wanted.

## Files

`vocal-assistant.tar.gz`: `scripts/install_stage1.sh`, `scripts/transcribe_wav.py`,
`scripts/bench_stt.py`, `scripts/stage2_preflight.sh`, `systemd/wyoming-whisper.service`,
`stage2/docker-compose.yml` (upstream v1.1.15 compose with bind-mounted custom wake
words and sounds), `stage2/env.example`, `tests/mock_stt.py`, `docs/STAGE1.md`,
`docs/STAGE2.md`. Stage 1 is installed on the Pi at `~/vocal-assistant` (hidden venv
`.venv`, config `whisper.env`, service `wyoming-whisper`, port 10300).
