# Voice assistant: handoff brief for a future Claude Code agent

Written 2026-10-26. State as of 2026-10-14, when the last hands-on work happened; Andre
reported on 2026-10-26 that "everything has been working ok for a while". Nothing after
2026-10-14 was re-verified, so check before assuming.

**Read first:** `architecture/voice-assistant.md` (full history, every check, 22 recorded
corrections) and `claude/working-agreement.md` (the three-streams rule). This brief is the
short version plus what to do next. Where the two disagree, the longer doc has the evidence.

## 1. Goal

A hands-free voice front end for Andre's personal assistant. He says "okay computer" plus a
command in the main living area. Home Assistant (HA) handles home commands locally. Anything
HA cannot match is eventually passed to a conversation agent that calls futileMCP (the
`pa_*` tools). Stages: (1) speech-to-text, done; (2) mic, speaker, wake word, satellite,
done for the stock wake word; (3) spoken replies and vocabulary hints, partly done;
(4) fallback agent into futileMCP, not started.

## 2. What exists and works (verified)

| Piece | Where | State |
|---|---|---|
| Wyoming faster-whisper STT | Pi 5 `vocalassistant`, systemd unit `wyoming-whisper`, port 10300, installed at `~/vocal-assistant` (hidden `.venv`, config `whisper.env`) | Working. Model `base-int8`, `--compute-type int8`, about 1.8 s for a 1.5 s command |
| Linux Voice Assistant (LVA) 1.1.15 in Docker | Same Pi, `~/developer/vocal-assistant/stage2/`, container `linux-voice-assistant`, ESPHome port 6053 | Working. Adopted by HA. Survives a cold reboot with nobody logged in (checked 2026-10-14) |
| Mic and speaker | Seeed ReSpeaker XVF3800, plain USB board, speaker plugged into the array | Working. Hardware AEC, beamforming, noise suppression, AGC. Speaker must stay on the array so its AEC sees the playback |
| Wake word | Stock `okay_computer` (microWakeWord), sensitivity 0.90 | One-hour soak with zero false wakes (2026-10-14). Andre: "working well with no false positives" |
| Spoken reply | HA pipeline TTS plays through the Pi | Heard and clear at 85% volume (2026-10-14). TTS engine not recorded |
| Home Assistant | HA OS on a Pi 4, `192.168.86.252` in the satellite logs | Pipeline: LVA wake, then Whisper STT, then HA intent matching, then TTS |

Host facts: Pi 5, 64-bit Raspberry Pi OS Trixie, Python 3.13, headless, user `dremon`
(uid 1000), Wi-Fi `wlan0` 192.168.86.251 (by choice, no wired option near the mic),
DHCP reservation on the router. Docker is Debian's `docker.io` 26.1.5 plus
`docker-compose` 2.26.1-4, run with `sudo docker`. The user is deliberately not in the
`docker` group.

## 3. Settings that matter, and why

- `AUDIO_INPUT_CHANNELS=1`. LVA treats channel 1 as an echo reference for server-side AEC;
  the XVF3800's channel 1 is a second beam, so the default of 2 would mislabel it.
- `MIC_AUTO_GAIN` and `MIC_NOISE_SUPPRESSION` are **unset on purpose**. The array does both
  in hardware, and setting them in `.env` locks the HA switches.
- `NETWORK_INTERFACE="wlan0"` pinned. LVA derives its device identity from the interface
  MAC, so a later cable would otherwise look like a new device to HA.
- `AUDIO_OUTPUT_DEVICE="pulse/alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993701262801356-00.analog-stereo"`.
  Pinned because an HDMI sink exists and can become the default sink. The pin was tested
  (chime stayed on the array while the default pointed at HDMI).
- Volume 85% on the array sink. PipeWire percentages are cubic (amplitude = fraction^3),
  so 85% is 0.614, or -4.24 dB. The array's hardware mixer (`amixer -c Array sget PCM,0`,
  0 to 60 steps of 1 dB) tracks the sink, so there is one volume stage. Was 40% and
  "barely audible".
- Sensitivity 0.90, set in HA (device page, Configuration section, "Wake Word 1
  Sensitivity"). Stored in `preferences.json` inside the `configuration` Docker volume at
  `/app/configuration/preferences.json`. **After first start, `preferences.json` overrides
  `WAKE_MODEL`**; editing the env var does not change the wake word.
- Image tag pinned to `1.1.15`, never `latest`. Debug logging (`ENABLE_DEBUG`) is **off**;
  Docker's default json-file logs do not rotate, so only enable it for a test.
- `loginctl enable-linger dremon` is required (`Linger=yes`) or audio dies after a reboot
  with nobody logged in.

## 4. Decisions Andre has made (do not reopen without a reason)

- Stay with the stock **"okay computer"** wake word (2026-10-14). Custom wake word is
  deferred. If it ever returns, the lead candidate is "futile assistant" (longest, most
  distinctive phoneme sequence; matches futileMCP). Install `.tflite` plus JSON manifest
  into `stage2/wakewords-custom/`, then repeat hit-rate, soak and reboot checks.
- LVA, not `wyoming-satellite` (archived, 2023-era pins that need source builds on 3.13).
- Local-first routing: HA intent matching first, futileMCP fallback second.
- STT stays on the Pi 5 (the Pi 4 is too small). No accelerator HAT for now.
- **Voice-assistant steps are not to be added as tasks in futileMCP** (2026-10-11, "the xvf
  is a plain USB board; don't add those as steps right now"). Confirm with him before
  changing that.
- Whisper stays on Wyoming (10300); LVA talks to HA over ESPHome (6053, HA connects to the Pi).

## 5. Next steps, in order

1. **Establish current state first** (Andre may have changed things since 10-14). Ask him to run:
   `sudo docker ps --format '{{.Names}} {{.Status}}'`, `systemctl status wyoming-whisper`,
   `pactl get-sink-volume @DEFAULT_SINK@`, and say "okay computer, turn on/off a light".
2. **Record which HA pipeline and TTS engine are actually in use.** Not yet done. HA has three
   pipelines under Settings, Voice assistants. The satellite's own "Assistant" select
   (device page, Configuration) overrides the star-marked "preferred" one. Open the pipeline
   that shows Wyoming Whisper as its STT engine and note the TTS engine and voice. Add it to
   the doc.
3. **Stage 3 remaining work:**
   - Confirm replies play through the array itself (so hardware AEC hears them), not some
     other sink. The output pin should guarantee it, but test barge-in: speak over a long
     reply and see whether wake detection still fires.
   - Whisper vocabulary hints from HA entity and area names (`--hass-token`, token in a
     mode 600 file, never in the unit file or the repo). Do this only if the accuracy
     sample below shows a real problem.
4. **Broader STT accuracy sample.** All measurements so far are synthetic speech, the easy
   case. Record phrases on a phone using real HA room and device names, convert with
   `sox in.wav -r 16000 -c 1 -b 16 /tmp/test.wav`, run through `transcribe_wav.py`.
5. **Stage 4: fallback agent into futileMCP.** Unmatched commands go to a conversation agent
   that calls `pa_*` tools. Not started. Open question: HA's MCP client integration versus a
   custom conversation agent. **Verify against the installed HA version (2026.8 or later)
   before proposing**; do not design from memory. Also decide which `pa_*` tools a spoken
   request may call, and keep writes narrow (see `claude/task-agent-brief.md` section 5 for
   the known traps: `pa_recurrence_add` is create-only and permanent; `pa_task_update`
   silently ignores `contexts`; dates cannot be cleared).
6. **Small open items:** the ten-try natural-pronunciation hit rate at 0.90 was never reported;
   whether the Pi 5 is on the tailnet (a `100.x` address appeared in the preflight);
   Companion app internal URL (optional); consider capping container logs
   (`logging: options: max-size`) in the compose file so debug can stay available.
7. **Update `architecture/voice-assistant.md`.** Its "Decisions made" section still has a
   stale Wake word bullet ("custom, on the Pi ... Final pick after the soak test"); the
   decision is now stock "okay computer". Read, edit, write the full file back.

## 6. How to work on this (rules that came out of the machine)

- **Only behaviour proves a setting.** Process-is-up proves nothing: the model loads lazily,
  and a package or config change is not live until the service is restarted or the container
  recreated. Verify with `docker compose config`, `uptime -s`, or a real transcription.
- **An empty result needs a positive control.** Run a broader grep that must match something
  before trusting "nothing happened".
- **Confirm a setting from the file or log that stores it**, not from a description (the
  "sensitivity is 85%" misread was actually the volume; `preferences.json` showed `null`).
- **Check the current docs and the actual schema before giving option names.** Add-on and HA
  options change; an unknown key silently reverts.
- `docker compose` commands only work inside `~/developer/vocal-assistant/stage2/`;
  `sudo docker logs linux-voice-assistant 2>&1` works anywhere.
- Wake detections log at debug level only (`Detected wake word: ...` and
  `Wake word '...' activated (probability X exceeded threshold Y)`); turn debug off again
  after any test.
- **Environment limits:** the cloud workspace cannot reach `ghcr.io` or `download.docker.com`
  (egress policy), and cannot touch the Pi or HA directly unless the session is linked to
  Andre's computer. Everything on the Pi has so far been done by giving Andre exact commands
  and reading his pasted output. Say so plainly rather than implying access you do not have.
- **Streams:** this is **Build** work in `claude/working-agreement.md` terms. In a Life
  session, a voice fault becomes a captured task and nothing more. Do not reorganise his life
  here; capture it and move on.
- Ask Andre clarifying questions; he wants the reasoning explained (CS degree, Python
  primary, would like to use Go more). He pastes terminal output back, so give one command
  block at a time, say what a good result looks like before he runs it, and say what each
  command proves.

## 7. Files

`vocal-assistant.tar.gz` (delivered to Andre; unpacked on the Pi at `~/developer/vocal-assistant`):
`scripts/install_stage1.sh`, `scripts/transcribe_wav.py`, `scripts/bench_stt.py`,
`scripts/stage2_preflight.sh`, `scripts/stage2_audio_check.sh`, `systemd/wyoming-whisper.service`,
`stage2/docker-compose.yml`, `stage2/env.example`, `tests/mock_stt.py`, `docs/STAGE1.md`,
`docs/STAGE2.md`. The live `stage2/.env` exists only on the Pi. This copy of the bundle was
built in a cloud scratchpad that does not persist, so if the files are needed, ask Andre to
paste them from the Pi.
