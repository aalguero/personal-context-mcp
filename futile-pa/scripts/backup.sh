#!/usr/bin/env bash
#
# Encrypted, verified backup of the futile-pa system: the Postgres database,
# context.json, .env, and enough of the systemd unit to rebuild from bare
# metal. Ships to a local server copy, an rsync target on vocalassistant (the
# always-on Pi), and B2 — whichever of those destinations are configured.
#
# Encryption key: PA_BACKUP_AGE_RECIPIENT is a public age key. This machine
# can create backups it cannot read; that only holds if nobody ever puts the
# matching private key here. Nothing in this script should ever decrypt.
#
# Run manually: scripts/backup.sh
# Run by timer: futile-pa-backup.service (see the systemd unit)

set -euo pipefail   # a backup script that continues after an error is how
                    # you get empty backups

# Every tool this script runs comes from /usr/bin (Debian packages). The
# hand-installed age/rclone/rsync that once lived in ~/.local/bin were retired
# on 2026-10-10; that prepend is gone too, so nothing dropped there later can
# silently shadow the packaged binaries.

REPO_ROOT="/home/dremon/tools/personal-context-mcp"
ENV_FILE="$REPO_ROOT/.env"
PA_PATH="$REPO_ROOT/futile-pa"
VENV_PYTHON="$REPO_ROOT/.venv-new/bin/python"
STATUS_FILE="/home/dremon/.personal-context/backup-status.json"
LOCAL_COPY_DIR="/home/dremon/backups"
# Tailscale SSH authorizes by tailnet policy and login *user*, not by key —
# it never reads authorized_keys, so a dedicated keypair here does nothing.
# The policy accepts non-root logins to Andre's own devices. The target used
# to be darkslip, a laptop that is off at 03:15, so it almost never landed;
# vocalassistant is a Pi that has to be on. The Pi never holds the age private
# key, so what lands there is inert. Its host key is pinned in
# ~/.ssh/known_hosts (verified against the tailnet record) -- BatchMode
# refuses an unknown key rather than prompting.
PI_SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
PI_HOST="dremon@vocalassistant"
PI_REMOTE_DIR="/home/dremon/backups/futile-pa"
PI_DEST="$PI_HOST:$PI_REMOTE_DIR/"
DB_NAME="futile_assistant"
DB_ROLE="musicbrainz"
VERIFY_DB="pa_verify"

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_STARTED="$(date --iso-8601=seconds)"

# ---------------------------------------------------------------------------
# 1. Read .env for the variables this script needs. Never echo any of them.
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
    echo "backup.sh: $ENV_FILE not found" >&2
    exit 1
fi

# Export only the lines we recognise, rather than sourcing the whole file
# blindly (the file also carries PA_AUTH_TOKEN and PA_AUTH_ALLOW_PATH, which
# this script has no business reading into its environment).
load_env_var() {
    local key="$1"
    local line
    line="$(grep -m1 "^${key}=" "$ENV_FILE" || true)"
    if [[ -n "$line" ]]; then
        printf '%s\n' "${line#*=}"
    fi
}

PGPASSWORD="$(load_env_var PGPASSWORD)"
PGUSER_VAL="$(load_env_var PGUSER)"
PA_DATABASE_URL="$(load_env_var PA_DATABASE_URL)"
PA_BACKUP_AGE_RECIPIENT="$(load_env_var PA_BACKUP_AGE_RECIPIENT)"
PA_BACKUP_B2_BUCKET="$(load_env_var PA_BACKUP_B2_BUCKET)"
PA_BACKUP_B2_KEY_ID="$(load_env_var PA_BACKUP_B2_KEY_ID)"
PA_BACKUP_B2_APP_KEY="$(load_env_var PA_BACKUP_B2_APP_KEY)"

if [[ -z "$PA_BACKUP_AGE_RECIPIENT" ]]; then
    echo "backup.sh: PA_BACKUP_AGE_RECIPIENT is not set — refusing to run unencrypted" >&2
    exit 1
fi

export PGPASSWORD
export PGUSER="${PGUSER_VAL:-$DB_ROLE}"

B2_CONFIGURED=0
if [[ -n "$PA_BACKUP_B2_BUCKET" && -n "$PA_BACKUP_B2_KEY_ID" && -n "$PA_BACKUP_B2_APP_KEY" ]]; then
    B2_CONFIGURED=1
fi

# ---------------------------------------------------------------------------
# 3. Work in a scratch dir that dies with this process, whatever happens.
# ---------------------------------------------------------------------------
WORK="$(mktemp -d /tmp/futile-pa-backup.XXXXXX)"
SIGNAL_STOPPED=0
# signal_restart is in the trap so that no exit path -- set -e, a signal,
# a failed tar -- can leave the Signal bot stopped (see section 6b).
cleanup() { signal_restart; rm -rf "$WORK"; }
trap cleanup EXIT
trap 'exit 143' TERM INT HUP   # route signals through the EXIT trap

RESULT="ok"
ERROR_MSG="null"
VERIFIED="false"
SMOKE_LINE=""
ENCRYPTED_BYTES=0
ROLES_INCLUDED="false"
DEST_LOCAL="skipped"
DEST_PI="skipped"
DEST_B2="skipped"

fail() {
    RESULT="failed"
    ERROR_MSG="$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')"
    echo "backup.sh: FAILED: $1" >&2
}

# A destination (vocalassistant/B2) failing does not make the backup untrustworthy
# -- verification and encryption already happened, and the local copy is
# real. It just means this run has one fewer off-machine copy, which is what
# off_machine_copies/the ok-vs-degraded split exists to say precisely. So this
# logs without touching RESULT; the top-level "error" field stays reserved
# for "nothing trustworthy exists" (see fail() above).
dest_error() {
    echo "backup.sh: $1" >&2
}

# ---------------------------------------------------------------------------
# Signal account data (used in 6b). The bot's whole Signal identity lives in
# this bind-mounted directory: losing it means re-registering the number,
# and without the PIN, a 7-day registration lock.
#
# RESTORE IS FOR DISASTER RECOVERY ONLY -- NEVER FOR CLONING. Restoring this
# directory onto a second machine while the original still runs puts two
# processes on one Signal account with divergent session state, which Signal
# will break, possibly taking the account with it. The annual restore drill
# (recurrence 8, February) must restore the database WITHOUT restoring this
# directory.
#
# Copied cold: signal-cli writes session state continuously, and a hot copy
# can catch a torn write -- an archive that looks fine and will not restore.
# So the container is stopped for the few seconds the tar takes, and
# signal_restart runs on every path out, including the EXIT trap.
# ---------------------------------------------------------------------------
SIGNAL_DIR="/home/dremon/.signal-cli-rest-api"
SIGNAL_CONTAINER="signal-cli-rest-api"
SIGNAL_INCLUDED="false"
SIGNAL_BYTES=0

signal_restart() {
    [[ "$SIGNAL_STOPPED" == "1" ]] || return 0
    SIGNAL_STOPPED=0
    if ! docker start "$SIGNAL_CONTAINER" >/dev/null 2>&1; then
        echo "backup.sh: FAILED to restart $SIGNAL_CONTAINER — the Signal bot is down" >&2
    fi
}

# Writes the tarball into $1. Only stops a container that was running, and
# only restarts what it stopped: a container someone stopped deliberately
# stays stopped (and is already quiescent, so a cold copy needs no stop).
copy_signal_data() {
    local out="$1/signal-cli-data.tar.gz"
    if [[ ! -d "$SIGNAL_DIR" ]]; then
        dest_error "Signal data directory missing — bundle has no Signal account data"
        return 0
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$SIGNAL_CONTAINER" 2>/dev/null)" == "true" ]]; then
        if docker stop "$SIGNAL_CONTAINER" >/dev/null 2>&1; then
            SIGNAL_STOPPED=1
        else
            dest_error "could not stop $SIGNAL_CONTAINER — Signal data not copied (a hot copy can tear)"
            return 0
        fi
    fi
    # Exit status only, never tar's output: file names inside are the account.
    if tar -czf "$out" -C "$(dirname "$SIGNAL_DIR")" "$(basename "$SIGNAL_DIR")" 2>/dev/null; then
        SIGNAL_INCLUDED="true"
        SIGNAL_BYTES="$(stat -c%s "$out")"
    else
        rm -f "$out"
        dest_error "Signal data tar failed — bundle has no Signal account data"
    fi
    signal_restart
}

# ---------------------------------------------------------------------------
# 4. Dump, in both formats.
# ---------------------------------------------------------------------------
DUMP_FC="$WORK/futile_assistant.dump"
DUMP_SQL_GZ="$WORK/futile_assistant.sql.gz"

if ! pg_dump -h localhost -U "$PGUSER" -Fc "$DB_NAME" -f "$DUMP_FC"; then
    fail "pg_dump -Fc failed"
fi

if [[ "$RESULT" == "ok" ]]; then
    if ! pg_dump -h localhost -U "$PGUSER" -Fp "$DB_NAME" | gzip > "$DUMP_SQL_GZ"; then
        fail "pg_dump -Fp (plain SQL) failed"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Verify BEFORE encrypting. This is the point of the exercise: the server
#    cannot decrypt its own backups (no private key here), so plaintext
#    verification against a throwaway database is the only check that will
#    ever run before the file leaves this machine for good.
# ---------------------------------------------------------------------------
if [[ "$RESULT" == "ok" ]]; then
    if psql -h localhost -U "$PGUSER" -d postgres -tAc \
        "select 1 from pg_database where datname = '$VERIFY_DB'" | grep -q 1; then
        dropdb -h localhost -U "$PGUSER" "$VERIFY_DB" || true
    fi

    if ! createdb -h localhost -U "$PGUSER" "$VERIFY_DB"; then
        fail "createdb $VERIFY_DB failed"
    fi
fi

if [[ "$RESULT" == "ok" ]]; then
    if ! pg_restore -h localhost -U "$PGUSER" -d "$VERIFY_DB" "$DUMP_FC" 2>"$WORK/restore.log"; then
        fail "pg_restore into $VERIFY_DB failed: $(tail -c 500 "$WORK/restore.log")"
    fi
fi

if [[ "$RESULT" == "ok" ]]; then
    VERIFY_URL="postgresql://${PGUSER}:${PGPASSWORD}@localhost:5432/${VERIFY_DB}"
    SMOKE_OUT="$WORK/smoke.log"
    if PA_DATABASE_URL="$VERIFY_URL" "$VENV_PYTHON" "$PA_PATH/tests/test_smoke.py" \
        > "$SMOKE_OUT" 2>&1; then
        SMOKE_LINE="$(grep -E '[0-9]+ passed' "$SMOKE_OUT" | head -1 || true)"
        VERIFIED="true"
    else
        SMOKE_LINE="$(grep -E '[0-9]+ passed' "$SMOKE_OUT" | head -1 || true)"
        # The 2026-10-28 incident took a scratch restore and a week to
        # diagnose because only the summary line ("N passed, M failed")
        # reached the status file -- the actual failing check's name and
        # detail were in smoke.log, which the EXIT trap deletes with $WORK.
        # Pulling the FAIL lines into the error message is the fix: they
        # survive into the status file and the journal, where a human
        # (or the watchdog's alert notes) can see them without a restore.
        FAIL_LINES="$(grep -E '^\s*FAIL\s' "$SMOKE_OUT" | head -5 || true)"
        fail "smoke test failed against restored dump: ${SMOKE_LINE:-see smoke.log}${FAIL_LINES:+ | ${FAIL_LINES}}"
    fi
fi

# Drop the verify database regardless of outcome — it is scratch, not backup.
dropdb -h localhost -U "$PGUSER" "$VERIFY_DB" 2>/dev/null || true

if [[ "$RESULT" != "ok" ]]; then
    echo "backup.sh: verification failed, nothing will be encrypted or shipped" >&2
fi

# ---------------------------------------------------------------------------
# 6/7. Build the archive and encrypt it. Only reached if verification passed.
# ---------------------------------------------------------------------------
ENCRYPTED_FILE=""
if [[ "$RESULT" == "ok" ]]; then
    BUNDLE="$WORK/bundle"
    mkdir -p "$BUNDLE"
    cp "$DUMP_FC" "$DUMP_SQL_GZ" "$BUNDLE/"

    if [[ -f /home/dremon/.personal-context/context.json ]]; then
        mkdir -p "$BUNDLE/personal-context"
        cp -a /home/dremon/.personal-context/context.json* "$BUNDLE/personal-context/" 2>/dev/null || true
    fi

    cp "$ENV_FILE" "$BUNDLE/env"

    if [[ -f "$HOME/.config/systemd/user/personal-context-mcp.service" ]]; then
        cp "$HOME/.config/systemd/user/personal-context-mcp.service" "$BUNDLE/"
    fi

    # Roles are cluster-global (pg_authid), so pg_dump never includes them --
    # yet the dump's grants depend on musicbrainz, pa_reader, mb_analytics_ro
    # and postgres. Without this a bare-metal restore yields a database nobody
    # may read. --no-role-passwords: musicbrainz's password is already in
    # bundle/env; no reason to also ship a hash to attack offline. Non-fatal:
    # the data itself is still restorable (pg_restore --no-privileges), so a
    # failure here is recorded in the status file rather than failing the run.
    if pg_dumpall -h localhost -U "$PGUSER" -l postgres --globals-only \
        --no-role-passwords > "$BUNDLE/globals.sql"; then
        ROLES_INCLUDED="true"
    else
        rm -f "$BUNDLE/globals.sql"
        echo "backup.sh: pg_dumpall --globals-only failed — bundle has no role definitions" >&2
    fi

    # 6b. Signal account data, inside the bundle so it is age-encrypted with
    # everything else. A failure here is recorded (signal_included false, and
    # the no_signal_data alert) without touching RESULT -- the same reasoning
    # as no_off_site and globals.sql: the database backup is still good, and
    # degrading the run would raise a false no_off_machine alert and stop
    # pruning on the Pi.
    copy_signal_data "$BUNDLE"

    git -C "$REPO_ROOT" rev-parse HEAD > "$BUNDLE/git-head.txt" 2>/dev/null \
        || echo "unknown" > "$BUNDLE/git-head.txt"

    # The tar goes to a file first (not a pipe) so its listing can be taken
    # from the exact bytes age encrypts: the status file then records what
    # the archive contains, checkable without the private key.
    ENCRYPTED_FILE="$WORK/futile-pa-$STAMP.tar.age"
    BUNDLE_TAR="$WORK/bundle.tar"
    if tar -C "$WORK" -cf "$BUNDLE_TAR" "bundle" \
        && tar -tvf "$BUNDLE_TAR" > "$WORK/bundle-listing.txt" \
        && age -r "$PA_BACKUP_AGE_RECIPIENT" -o "$ENCRYPTED_FILE" "$BUNDLE_TAR"; then
        :
    else
        fail "tar/age encryption failed"
        ENCRYPTED_FILE=""
    fi
    rm -f "$BUNDLE_TAR"
fi

# Every Postgres client call is above this line. Nothing below needs the
# password in its environment -- age, rsync, ssh and rclone would only
# inherit it, and the task blocks connect via an explicit PA_DATABASE_URL.
unset PGPASSWORD

CHECKSUM_FILE=""
if [[ "$RESULT" == "ok" && -n "$ENCRYPTED_FILE" ]]; then
    ENCRYPTED_BYTES="$(stat -c%s "$ENCRYPTED_FILE")"
    CHECKSUM_FILE="$ENCRYPTED_FILE.sha256"
    ( cd "$WORK" && sha256sum "$(basename "$ENCRYPTED_FILE")" > "$CHECKSUM_FILE" )
fi

# ---------------------------------------------------------------------------
# 8. Ship to each configured destination. Verify by listing + checksum, not
#    by exit code alone.
# ---------------------------------------------------------------------------
if [[ "$RESULT" == "ok" && -n "$ENCRYPTED_FILE" ]]; then
    BASENAME="$(basename "$ENCRYPTED_FILE")"

    # -- local, on-server copy --------------------------------------------
    if mkdir -p "$LOCAL_COPY_DIR" \
        && cp "$ENCRYPTED_FILE" "$CHECKSUM_FILE" "$LOCAL_COPY_DIR/" \
        && [[ "$(stat -c%s "$LOCAL_COPY_DIR/$BASENAME")" == "$ENCRYPTED_BYTES" ]]; then
        DEST_LOCAL="ok"
    else
        DEST_LOCAL="failed"
        fail "local copy to $LOCAL_COPY_DIR did not verify"
    fi

    # -- vocalassistant, over SSH ------------------------------------------
    if [[ "$RESULT" == "ok" ]]; then
        if rsync -a -e "ssh $PI_SSH_OPTS" \
            --mkpath "$ENCRYPTED_FILE" "$CHECKSUM_FILE" "$PI_DEST"; then
            REMOTE_SIZE="$(ssh $PI_SSH_OPTS "$PI_HOST" \
                "stat -c%s $PI_REMOTE_DIR/$BASENAME" 2>/dev/null || true)"
            if [[ "$REMOTE_SIZE" == "$ENCRYPTED_BYTES" ]]; then
                DEST_PI="ok"
                # Catch-up: the Pi is always on, but a network blip or reboot
                # can still miss a night. Repair that now by sending any local
                # archive it doesn't already have — best-effort, so a slow or
                # partial catch-up never turns tonight's good backup into a
                # failure.
                rsync -a -e "ssh $PI_SSH_OPTS" --ignore-existing \
                    "$LOCAL_COPY_DIR"/futile-pa-*.tar.age \
                    "$LOCAL_COPY_DIR"/futile-pa-*.tar.age.sha256 \
                    "$PI_DEST" 2>/dev/null || true
            else
                DEST_PI="failed"
                dest_error "vocalassistant copy size mismatch (local $ENCRYPTED_BYTES, remote ${REMOTE_SIZE:-unreachable})"
            fi
        else
            DEST_PI="failed"
            dest_error "rsync to vocalassistant failed"
        fi
    fi

    # -- B2, via rclone -------------------------------------------------
    # The bucket is object-locked (compliance, 30 days): append-only, and
    # nothing uploaded can be deleted or overwritten until it expires.
    #
    # One copyto per file: `rclone copy` takes exactly one source, and the
    # two-source form this replaced failed at argument parsing on every run
    # it ever got. --immutable makes an existing object a loud error rather
    # than a silent replacement.
    #
    # Credentials ride on each rclone command line as per-command env
    # assignments, never exported, so no other child of this script inherits
    # them. rclone's stderr goes to a scratch file and is logged with the
    # bucket name masked, so a usage or API error doesn't put it in the journal.
    b2_rclone() {
        RCLONE_CONFIG_B2_TYPE="b2" \
        RCLONE_CONFIG_B2_ACCOUNT="$PA_BACKUP_B2_KEY_ID" \
        RCLONE_CONFIG_B2_KEY="$PA_BACKUP_B2_APP_KEY" \
            rclone "$@" 2>>"$WORK/rclone.log"
    }
    b2_log_tail() {
        local log
        log="$(tail -n 5 "$WORK/rclone.log" 2>/dev/null || true)"
        printf '%s' "${log//"$PA_BACKUP_B2_BUCKET"/<bucket>}"
    }
    if [[ "$RESULT" == "ok" ]]; then
        if [[ "$B2_CONFIGURED" == "1" ]]; then
            B2_DIR="b2:$PA_BACKUP_B2_BUCKET/futile-pa"
            if b2_rclone copyto --immutable "$ENCRYPTED_FILE" "$B2_DIR/$BASENAME" \
                && b2_rclone copyto --immutable "$CHECKSUM_FILE" "$B2_DIR/$BASENAME.sha256"; then
                # Verify by content, not size: two archives of the same length
                # are the likely case at a stable dump size, not a coincidence.
                # --download hashes the bytes B2 actually serves back, end to
                # end, against the sha256 computed here before upload.
                LOCAL_SHA="$(cut -d' ' -f1 "$CHECKSUM_FILE")"
                REMOTE_SHA="$(b2_rclone hashsum sha256 --download "$B2_DIR/$BASENAME" \
                    | cut -d' ' -f1 || true)"
                if [[ -n "$REMOTE_SHA" && "$REMOTE_SHA" == "$LOCAL_SHA" ]]; then
                    DEST_B2="ok"
                else
                    DEST_B2="failed"
                    dest_error "B2 copy sha256 mismatch (local $LOCAL_SHA, remote ${REMOTE_SHA:-unreadable}) $(b2_log_tail)"
                fi
            else
                DEST_B2="failed"
                dest_error "rclone upload to B2 failed: $(b2_log_tail)"
            fi
        else
            DEST_B2="skipped (not configured)"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 8b. Classify the run. "ok" and "degraded" both mean verification, encryption
#     and the local copy all happened -- the difference is whether a copy also
#     left this machine. "failed" means none of that can be trusted (fail()
#     already set it). Computed here, once, rather than threading a third
#     state through every destination check above.
#
#     Off-machine is not off-site: vocalassistant is in the same house, so one
#     fire, flood or burglary takes it and this server together. Only B2
#     counts as off-site. degraded stays keyed on off-machine alone, because
#     degraded also stops remote pruning: a B2 outage must not quietly stop
#     retention on the Pi. off_site_copies == 0 raises its own alert instead
#     (no_off_site, in backup_alerts.py), independent of RESULT.
# ---------------------------------------------------------------------------
OFF_MACHINE_COPIES=0
OFF_SITE_COPIES=0
[[ "$DEST_PI" == "ok" ]] && OFF_MACHINE_COPIES=$((OFF_MACHINE_COPIES + 1))
if [[ "$DEST_B2" == "ok" ]]; then
    OFF_MACHINE_COPIES=$((OFF_MACHINE_COPIES + 1))
    OFF_SITE_COPIES=$((OFF_SITE_COPIES + 1))
fi

if [[ "$RESULT" == "ok" && "$OFF_MACHINE_COPIES" -eq 0 ]]; then
    RESULT="degraded"
fi

# ---------------------------------------------------------------------------
# 9. Prune — local on ok or degraded, vocalassistant only on ok, never on
#    failed, and never below one copy.
#    The GFS decision itself lives in prune_backups.py (easier to get right,
#    and to verify, than inline awk date arithmetic). It is a pure function
#    of filenames, so the local and vocalassistant legs feed it a listing and
#    then do their own deleting — the Pi never needs Python installed.
# ---------------------------------------------------------------------------
PRUNE_HELPER="$PA_PATH/scripts/prune_backups.py"

prune_local() {
    local dir="$1"
    local to_delete
    to_delete=$(find "$dir" -maxdepth 1 -name 'futile-pa-*.tar.age' -printf '%f\n' \
        | python3 "$PRUNE_HELPER")
    [[ -z "$to_delete" ]] && return 0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        rm -f "$dir/$f" "$dir/$f.sha256"
    done <<< "$to_delete"
}

prune_pi() {
    local to_delete
    to_delete=$(ssh $PI_SSH_OPTS "$PI_HOST" \
        "find $PI_REMOTE_DIR -maxdepth 1 -name 'futile-pa-*.tar.age' -printf '%f\n'" \
        2>/dev/null | python3 "$PRUNE_HELPER")
    [[ -z "$to_delete" ]] && return 0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        # -n: without it ssh reads this loop's stdin (the here-string) and
        # swallows every filename after the first, so only one gets pruned.
        ssh -n $PI_SSH_OPTS "$PI_HOST" \
            "rm -f $PI_REMOTE_DIR/$f $PI_REMOTE_DIR/$f.sha256" 2>/dev/null || true
    done <<< "$to_delete"
}

# Local pruning is safe on a degraded run too: tonight produced a verified
# local copy, so retention has something trustworthy to retain. Skipping it
# meant a week of deferred retention executing at once on the first ok run
# (five archives on 2026-10-16), and unbounded growth if the Pi stays down.
# Remote pruning is not safe on degraded: the destination is by definition in
# an unknown state.
#
# B2 is never pruned, deliberately -- do not add a B2 prune here. The bucket
# uses object lock so that a compromised server cannot erase backup history;
# a delete would fail every night and train people to stop reading the
# status file. Expiry belongs to the bucket's lifecycle rules (console).
if [[ "$RESULT" == "ok" || "$RESULT" == "degraded" ]]; then
    [[ "$DEST_LOCAL" == "ok" ]] && prune_local "$LOCAL_COPY_DIR"
fi
if [[ "$RESULT" == "ok" ]]; then
    [[ "$DEST_PI" == "ok" ]] && prune_pi
else
    echo "backup.sh: result=$RESULT — remote pruning skipped" >&2
fi

# ---------------------------------------------------------------------------
# 9b. Weekly re-check of what already sits on vocalassistant. The transfer is
#     verified on arrival, but an SD card can corrupt a file months later and
#     nothing would notice until the day it was needed. The .sha256 files
#     shipped next to every archive make this nearly free. (B2 needs none of
#     this: the upload is verified by downloading it back, and the bucket is
#     immutable.)
#
#     Due when the last *completed* check is more than six days old, rather
#     than on a fixed weekday: a Pi that is down on check night gets retried
#     the next night instead of a week later. Strictly an audit -- whatever
#     happens here never changes RESULT or the exit code, and nothing is
#     deleted. A mismatch raises pi_corruption; unreachable records "skipped".
# ---------------------------------------------------------------------------
PI_INTEGRITY="not_run"
PI_INTEGRITY_FILES=0
PI_INTEGRITY_OUT="$WORK/pi_integrity.out"
: > "$PI_INTEGRITY_OUT"

# Runs on the Pi via sh -s, so it must stay POSIX sh. Exit 0 = all match,
# 1 = mismatch or missing file, 3 = the backup directory itself is gone.
# Prints "checked N" last, and any failure lines before it.
pi_integrity_check() {
    local dir="$1"
    timeout 300 ssh $PI_SSH_OPTS "$PI_HOST" sh -s -- "$dir" <<'REMOTE'
cd "$1" 2>/dev/null || { echo "backup directory $1 missing"; exit 3; }
status=0; n=0
for f in futile-pa-*.tar.age; do
    [ -e "$f" ] || continue
    n=$((n + 1))
    [ -f "$f.sha256" ] || { echo "$f: no .sha256 file"; status=1; }
done
for s in futile-pa-*.tar.age.sha256; do
    [ -e "$s" ] || continue
    sha256sum -c --quiet "$s" 2>&1 || status=1
done
echo "checked $n"
exit $status
REMOTE
}

PI_INTEGRITY_DUE="$(python3 - "$STATUS_FILE" <<'PYEOF' || echo 1
import datetime as dt, json, sys
try:
    last = json.load(open(sys.argv[1])).get("pi_integrity_checked")
except (OSError, ValueError):
    last = None
if not last:
    print(1)
else:
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last)
    print(1 if age > dt.timedelta(days=6) else 0)
PYEOF
)"

if [[ "$PI_INTEGRITY_DUE" == "1" ]]; then
    if [[ "$DEST_PI" == "failed" ]]; then
        # Already failed to reach it tonight; don't spend another timeout.
        PI_INTEGRITY="skipped"
    else
        PI_RC=0
        pi_integrity_check "$PI_REMOTE_DIR" > "$PI_INTEGRITY_OUT" 2>&1 || PI_RC=$?
        case "$PI_RC" in
            0) PI_INTEGRITY="ok" ;;
            1|3) PI_INTEGRITY="mismatch" ;;
            *) PI_INTEGRITY="skipped" ;;   # 255 ssh, 124 timeout, anything else
        esac
        PI_INTEGRITY_FILES="$(sed -n 's/^checked \([0-9]*\)$/\1/p' "$PI_INTEGRITY_OUT" | tail -1)"
        PI_INTEGRITY_FILES="${PI_INTEGRITY_FILES:-0}"
        sed -i '/^checked [0-9]*$/d' "$PI_INTEGRITY_OUT"
    fi
    if [[ "$PI_INTEGRITY" == "mismatch" ]]; then
        echo "backup.sh: vocalassistant integrity check FAILED (nothing deleted):" >&2
        sed 's/^/backup.sh:   /' "$PI_INTEGRITY_OUT" >&2
    elif [[ "$PI_INTEGRITY" == "skipped" ]]; then
        echo "backup.sh: vocalassistant integrity check due but skipped (unreachable); retrying next run" >&2
    fi
fi

# ---------------------------------------------------------------------------
# 10. Status file, every run.
# ---------------------------------------------------------------------------
GIT_HEAD="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
NOW="$(date --iso-8601=seconds)"

mkdir -p "$(dirname "$STATUS_FILE")"
TMP_STATUS="$(mktemp)"
cat > "$TMP_STATUS" <<EOF
{
  "last_attempt": "$RUN_STARTED",
  "last_success": $( [[ "$RESULT" == "ok" ]] && printf '"%s"' "$NOW" || printf 'null' ),
  "last_verified_backup": $( [[ "$RESULT" == "ok" || "$RESULT" == "degraded" ]] && printf '"%s"' "$NOW" || printf 'null' ),
  "result": "$RESULT",
  "verified": $VERIFIED,
  "smoke_test": $( [[ -n "$SMOKE_LINE" ]] && printf '"%s"' "$SMOKE_LINE" || printf 'null' ),
  "git_head": "$GIT_HEAD",
  "encrypted_bytes": $ENCRYPTED_BYTES,
  "off_machine_copies": $OFF_MACHINE_COPIES,
  "off_site_copies": $OFF_SITE_COPIES,
  "roles_included": $ROLES_INCLUDED,
  "signal_included": $SIGNAL_INCLUDED,
  "signal_bytes": $SIGNAL_BYTES,
  "destinations": { "local": "$DEST_LOCAL", "vocalassistant": "$DEST_PI", "b2": "$DEST_B2" },
  "error": $ERROR_MSG
}
EOF

# last_success means "last run that was fully ok"; last_verified_backup means
# "last run that produced a trustworthy local copy, ok or degraded". Neither
# goes backwards: a run that didn't reach that bar keeps whatever the
# previous run already achieved.
#
# pi_integrity_checked/_files/_detail describe the last *completed* Pi check
# (ok or mismatch) and are carried forward on every run that doesn't complete
# one, so they mean "last time we looked", not "tonight". pi_integrity_result
# is the outcome of the last attempt: ok, mismatch, or skipped (due, but the
# Pi couldn't be reached).
python3 - "$TMP_STATUS" "$STATUS_FILE" "$PI_INTEGRITY" "$NOW" \
    "$PI_INTEGRITY_FILES" "$PI_INTEGRITY_OUT" "$WORK/bundle-listing.txt" <<'PYEOF' || true
import json, os, sys
tmp, status_file, pi, now, pi_files, pi_out, listing = sys.argv[1:]
d = json.load(open(tmp))
# bundle_contents: path -> bytes, from `tar -tv` of the exact tar that was
# encrypted. Names only (they are fixed), never contents. null if this run
# built no archive.
contents = {}
if os.path.exists(listing):
    for line in open(listing):
        parts = line.split()
        if len(parts) >= 6 and not parts[0].startswith('d'):
            contents[parts[5]] = int(parts[2])
d['bundle_contents'] = contents or None
prev = json.load(open(status_file)) if os.path.exists(status_file) else {}
if d['last_success'] is None:
    d['last_success'] = prev.get('last_success')
if d['last_verified_backup'] is None:
    d['last_verified_backup'] = prev.get('last_verified_backup')
for k in ('pi_integrity_checked', 'pi_integrity_result',
          'pi_integrity_files', 'pi_integrity_detail'):
    d[k] = prev.get(k)
if pi == 'skipped':
    d['pi_integrity_result'] = 'skipped'
elif pi in ('ok', 'mismatch'):
    d['pi_integrity_checked'] = now
    d['pi_integrity_result'] = pi
    d['pi_integrity_files'] = int(pi_files)
    d['pi_integrity_detail'] = [l for l in open(pi_out).read().splitlines() if l.strip()]
json.dump(d, open(tmp, 'w'), indent=2)
PYEOF

mv "$TMP_STATUS" "$STATUS_FILE"
chmod 600 "$STATUS_FILE"

# ---------------------------------------------------------------------------
# 11. Raise or close this script's warning tasks: failure, no_off_machine,
#     no_off_site, pi_corruption. One task per open condition, deduped and
#     auto-closed on pa.task.metadata->>'backup_alert', never on the title.
#     The rules live in backup_alerts.py, so a scratch-database test runs the
#     same code as production. Best-effort: a database problem here must not
#     turn a good backup into a failed one.
# ---------------------------------------------------------------------------
PA_DATABASE_URL="$PA_DATABASE_URL" "$VENV_PYTHON" "$PA_PATH/scripts/backup_alerts.py" \
    --result "$RESULT" --off-site-copies "$OFF_SITE_COPIES" \
    --pi-integrity "$PI_INTEGRITY" --pi-mismatches="$(cat "$PI_INTEGRITY_OUT")" \
    --run-started "$RUN_STARTED" --git-head "$GIT_HEAD" \
    --signal-included "$( [[ -n "$ENCRYPTED_FILE" ]] && echo "$SIGNAL_INCLUDED" || echo unknown )" \
    || echo "backup.sh: backup_alerts.py failed — alert tasks not updated" >&2

echo "backup.sh: result=$RESULT verified=$VERIFIED off_machine_copies=$OFF_MACHINE_COPIES off_site_copies=$OFF_SITE_COPIES roles_included=$ROLES_INCLUDED signal_included=$SIGNAL_INCLUDED signal_bytes=$SIGNAL_BYTES local=$DEST_LOCAL vocalassistant=$DEST_PI b2=$DEST_B2 pi_integrity=$PI_INTEGRITY"

# ---------------------------------------------------------------------------
# 12. External watchdog (task 182): is the check-in agent actually completing
#     runs, and did tonight keep both off-machine copies? Lives here because
#     this is a different unit, timer and process from the assistant it
#     watches, and it alerts by calling the Signal container directly. See
#     external_watchdog.py for what it asserts and why.
#
#     Last, after everything the backup does -- including the status file it
#     reads and copy_signal_data's container restart, which it waits on -- and
#     it cannot change this run's outcome: RESULT is final above, the call is
#     guarded with || so set -e never sees a failure, and timeout bounds it.
#     Deleting this section changes nothing else in the script.
# ---------------------------------------------------------------------------
external_watchdog() {
    PA_DATABASE_URL="$PA_DATABASE_URL" timeout 300 \
        "$VENV_PYTHON" "$PA_PATH/scripts/external_watchdog.py"
}
external_watchdog || echo "backup.sh: external_watchdog.py exited $? — watchdog checks incomplete" >&2

# ---------------------------------------------------------------------------
# 13. Backup dead man's switch (task 211): one ping to the external monitor,
#     only when RESULT is ok, so a run that never happens -- the timer dead,
#     the machine off -- lets a ~26h window lapse and the monitor emails
#     Andre. external_watchdog.py cannot see that case: it runs inside this
#     script. A run that happened and failed is not pinged: backup_alerts.py
#     and the watchdog already own that, and a ping would report the wrong
#     thing.
#
#     Same discipline as section 12: RESULT is final, the call is guarded with
#     || so set -e never sees a failure, and timeout bounds it. The URL is
#     read from .env inside the script, never passed on this command line.
#     Deleting this section changes nothing else in the script.
# ---------------------------------------------------------------------------
if [[ "$RESULT" == "ok" ]]; then
    timeout 30 "$VENV_PYTHON" -I "$PA_PATH/scripts/deadman_ping.py" --key BACKUP_PING_URL \
        || echo "backup.sh: deadman_ping.py exited $? — backup ping not sent" >&2
else
    echo "backup.sh: deadman ping skipped (result=$RESULT)"
fi

if [[ "$RESULT" == "failed" ]]; then
    exit 1
fi
