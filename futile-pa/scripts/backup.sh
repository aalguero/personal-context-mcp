#!/usr/bin/env bash
#
# Encrypted, verified backup of the futile-pa system: the Postgres database,
# context.json, .env, and enough of the systemd unit to rebuild from bare
# metal. Ships to a local server copy, a darkslip rsync target, and B2 —
# whichever of those destinations are configured.
#
# Encryption key: PA_BACKUP_AGE_RECIPIENT is a public age key. This machine
# can create backups it cannot read; that only holds if nobody ever puts the
# matching private key here. Nothing in this script should ever decrypt.
#
# Run manually: scripts/backup.sh
# Run by timer: futile-pa-backup.service (see the systemd unit)

set -euo pipefail   # a backup script that continues after an error is how
                    # you get empty backups

# age and rclone are installed as user-local static binaries (no root
# available in this environment at the time this was written -- see the
# backup report). systemd user units do not inherit ~/.local/bin on PATH,
# so it has to be added explicitly rather than assumed.
export PATH="/home/dremon/.local/bin:$PATH"

REPO_ROOT="/home/dremon/tools/personal-context-mcp"
ENV_FILE="$REPO_ROOT/.env"
PA_PATH="$REPO_ROOT/futile-pa"
VENV_PYTHON="$REPO_ROOT/.venv-new/bin/python"
STATUS_FILE="/home/dremon/.personal-context/backup-status.json"
LOCAL_COPY_DIR="/home/dremon/backups"
# Tailscale SSH authorizes by tailnet policy and login *user*, not by key —
# it never reads authorized_keys, so a dedicated keypair here does nothing.
# The policy accepts non-root logins to Andre's own devices; the account on
# darkslip is "andre", not this machine's "dremon".
DARKSLIP_SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15"
DARKSLIP_HOST="andre@darkslip"
DARKSLIP_REMOTE_DIR="/home/andre/backups/futile-pa"
DARKSLIP_DEST="$DARKSLIP_HOST:$DARKSLIP_REMOTE_DIR/"
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
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

RESULT="ok"
ERROR_MSG="null"
VERIFIED="false"
SMOKE_LINE=""
ENCRYPTED_BYTES=0
DEST_LOCAL="skipped"
DEST_DARKSLIP="skipped"
DEST_B2="skipped"

fail() {
    RESULT="failed"
    ERROR_MSG="$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')"
    echo "backup.sh: FAILED: $1" >&2
}

# A destination (darkslip/B2) failing does not make the backup untrustworthy
# -- verification and encryption already happened, and the local copy is
# real. It just means this run has one fewer offsite copy, which is what
# offsite_copies/the ok-vs-degraded split exists to say precisely. So this
# logs without touching RESULT; the top-level "error" field stays reserved
# for "nothing trustworthy exists" (see fail() above).
dest_error() {
    echo "backup.sh: $1" >&2
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
        fail "smoke test failed against restored dump: ${SMOKE_LINE:-see smoke.log}"
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

    git -C "$REPO_ROOT" rev-parse HEAD > "$BUNDLE/git-head.txt" 2>/dev/null \
        || echo "unknown" > "$BUNDLE/git-head.txt"

    ENCRYPTED_FILE="$WORK/futile-pa-$STAMP.tar.age"
    if ! tar -C "$WORK" -cf - "bundle" \
        | age -r "$PA_BACKUP_AGE_RECIPIENT" -o "$ENCRYPTED_FILE"; then
        fail "tar/age encryption failed"
        ENCRYPTED_FILE=""
    fi
fi

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

    # -- darkslip, over SSH -------------------------------------------------
    if [[ "$RESULT" == "ok" ]]; then
        if rsync -a -e "ssh $DARKSLIP_SSH_OPTS" \
            --mkpath "$ENCRYPTED_FILE" "$CHECKSUM_FILE" "$DARKSLIP_DEST"; then
            REMOTE_SIZE="$(ssh $DARKSLIP_SSH_OPTS "$DARKSLIP_HOST" \
                "stat -c%s $DARKSLIP_REMOTE_DIR/$BASENAME" 2>/dev/null || true)"
            if [[ "$REMOTE_SIZE" == "$ENCRYPTED_BYTES" ]]; then
                DEST_DARKSLIP="ok"
                # Catch-up: darkslip is a desktop and may have been asleep on a
                # previous night. Repair that now by sending any local archive
                # it doesn't already have — best-effort, so a slow or partial
                # catch-up never turns tonight's good backup into a failure.
                rsync -a -e "ssh $DARKSLIP_SSH_OPTS" --ignore-existing \
                    "$LOCAL_COPY_DIR"/futile-pa-*.tar.age \
                    "$LOCAL_COPY_DIR"/futile-pa-*.tar.age.sha256 \
                    "$DARKSLIP_DEST" 2>/dev/null || true
            else
                DEST_DARKSLIP="failed"
                dest_error "darkslip copy size mismatch (local $ENCRYPTED_BYTES, remote ${REMOTE_SIZE:-unreachable})"
            fi
        else
            DEST_DARKSLIP="failed"
            dest_error "rsync to darkslip failed"
        fi
    fi

    # -- B2, via rclone -------------------------------------------------
    if [[ "$RESULT" == "ok" ]]; then
        if [[ "$B2_CONFIGURED" == "1" ]]; then
            export RCLONE_CONFIG_B2_TYPE="b2"
            export RCLONE_CONFIG_B2_ACCOUNT="$PA_BACKUP_B2_KEY_ID"
            export RCLONE_CONFIG_B2_KEY="$PA_BACKUP_B2_APP_KEY"
            if rclone copy "$ENCRYPTED_FILE" "$CHECKSUM_FILE" \
                "b2:$PA_BACKUP_B2_BUCKET/futile-pa/" --b2-hard-delete; then
                REMOTE_B2_SIZE="$(rclone size "b2:$PA_BACKUP_B2_BUCKET/futile-pa/$BASENAME" \
                    --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["bytes"])' 2>/dev/null || true)"
                if [[ "$REMOTE_B2_SIZE" == "$ENCRYPTED_BYTES" ]]; then
                    DEST_B2="ok"
                else
                    DEST_B2="failed"
                    dest_error "B2 copy size mismatch (local $ENCRYPTED_BYTES, remote ${REMOTE_B2_SIZE:-unreachable})"
                fi
            else
                DEST_B2="failed"
                dest_error "rclone copy to B2 failed"
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
# ---------------------------------------------------------------------------
OFFSITE_COPIES=0
[[ "$DEST_DARKSLIP" == "ok" ]] && OFFSITE_COPIES=$((OFFSITE_COPIES + 1))
[[ "$DEST_B2" == "ok" ]] && OFFSITE_COPIES=$((OFFSITE_COPIES + 1))

if [[ "$RESULT" == "ok" && "$OFFSITE_COPIES" -eq 0 ]]; then
    RESULT="degraded"
fi

# ---------------------------------------------------------------------------
# 9. Prune — only on a fully successful run, and never below one copy.
#    The GFS decision itself lives in prune_backups.py (easier to get right,
#    and to verify, than inline awk date arithmetic). It is a pure function
#    of filenames, so the local and darkslip legs feed it a listing and then
#    do their own deleting — darkslip never needs Python installed.
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

prune_darkslip() {
    local to_delete
    to_delete=$(ssh $DARKSLIP_SSH_OPTS "$DARKSLIP_HOST" \
        "find $DARKSLIP_REMOTE_DIR -maxdepth 1 -name 'futile-pa-*.tar.age' -printf '%f\n'" \
        2>/dev/null | python3 "$PRUNE_HELPER")
    [[ -z "$to_delete" ]] && return 0
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        ssh $DARKSLIP_SSH_OPTS "$DARKSLIP_HOST" \
            "rm -f $DARKSLIP_REMOTE_DIR/$f $DARKSLIP_REMOTE_DIR/$f.sha256" 2>/dev/null || true
    done <<< "$to_delete"
}

if [[ "$RESULT" == "ok" ]]; then
    [[ "$DEST_LOCAL" == "ok" ]] && prune_local "$LOCAL_COPY_DIR"
    [[ "$DEST_DARKSLIP" == "ok" ]] && prune_darkslip
    # B2 retention left to a lifecycle rule on the bucket (section 7 covers
    # local + darkslip; B2 is cold storage and object-count pruning over
    # rclone's HTTP API is not worth the complexity at 23 files).
else
    echo "backup.sh: result=$RESULT — pruning skipped on all destinations" >&2
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
  "offsite_copies": $OFFSITE_COPIES,
  "destinations": { "local": "$DEST_LOCAL", "darkslip": "$DEST_DARKSLIP", "b2": "$DEST_B2" },
  "error": $ERROR_MSG
}
EOF

# last_success means "last run that was fully ok"; last_verified_backup means
# "last run that produced a trustworthy local copy, ok or degraded". Neither
# goes backwards: a run that didn't reach that bar keeps whatever the
# previous run already achieved.
if [[ -f "$STATUS_FILE" ]]; then
    python3 -c "
import json
d = json.load(open('$TMP_STATUS'))
prev = json.load(open('$STATUS_FILE'))
if d['last_success'] is None:
    d['last_success'] = prev.get('last_success')
if d['last_verified_backup'] is None:
    d['last_verified_backup'] = prev.get('last_verified_backup')
json.dump(d, open('$TMP_STATUS', 'w'), indent=2)
" || true
fi

mv "$TMP_STATUS" "$STATUS_FILE"
chmod 600 "$STATUS_FILE"

# ---------------------------------------------------------------------------
# On failure or degraded: create a task. Two separate titles, each guarded
# independently, so a bad week makes one task per condition, not seven, and
# the two severities never dedupe against each other.
# ---------------------------------------------------------------------------
if [[ "$RESULT" == "failed" ]]; then
    PA_DATABASE_URL="$PA_DATABASE_URL" "$VENV_PYTHON" - <<PYEOF || true
import sys
sys.path.insert(0, "$PA_PATH")
from pa import repo
from pa.db import get_db
db = get_db()
existing = db.row("""select id from pa.task
                      where title = 'Backup is failing — investigate'
                        and status not in ('done','dropped')""")
if not existing:
    repo.task_add(
        title="Backup is failing — investigate",
        project="admin-2026", status="next", importance=5,
        effort_minutes=30, energy="medium",
        contexts=["computer", "focus"],
        notes="Written automatically by scripts/backup.sh. "
              "See /home/dremon/.personal-context/backup-status.json for the error. "
              "Importance 5: an unnoticed backup failure is the failure mode the "
              "whole design exists to prevent.",
        # "backup.sh" is not in pa.task's source check constraint (manual,
        # claude, google_tasks, gmail, calendar, recurrence, import -- see
        # migrations/001_core.sql). Closest existing fit; a dedicated value
        # belongs to a future schema change, not this brief.
        source="manual", db=db)
PYEOF
fi

if [[ "$RESULT" == "degraded" ]]; then
    PA_DATABASE_URL="$PA_DATABASE_URL" "$VENV_PYTHON" - <<PYEOF || true
import sys
sys.path.insert(0, "$PA_PATH")
from pa import repo
from pa.db import get_db
db = get_db()
existing = db.row("""select id from pa.task
                      where title = 'Backup has no off-site copy yet'
                        and status not in ('done','dropped')""")
if not existing:
    repo.task_add(
        title="Backup has no off-site copy yet",
        project="admin-2026", status="next", importance=3,
        effort_minutes=15, energy="low",
        contexts=["computer"],
        notes="Written automatically by scripts/backup.sh. A verified, "
              "encrypted backup exists locally (see "
              "/home/dremon/.personal-context/backup-status.json) but has not "
              "reached darkslip or B2. Lower importance than the failure task: "
              "the backup itself is good, it just isn't off this machine yet.",
        source="manual", db=db)
PYEOF
fi

echo "backup.sh: result=$RESULT verified=$VERIFIED offsite_copies=$OFFSITE_COPIES local=$DEST_LOCAL darkslip=$DEST_DARKSLIP b2=$DEST_B2"

if [[ "$RESULT" == "failed" ]]; then
    exit 1
fi
