#!/usr/bin/env bash
set -euo pipefail

: "${PRODUCTION_READ_ONLY_DATABASE_URL:?missing read-only source URL}"
: "${RESTORE_ARTIFACT_DIR:?missing restore artifact directory}"
: "${RESTORE_SCRATCH_DIR:?missing restore scratch directory}"
: "${RUNNER_TEMP:?missing runner temp directory}"
: "${GITHUB_WORKSPACE:?missing GitHub workspace directory}"
: "${GITHUB_RUN_ID:?missing GitHub run ID}"
: "${GITHUB_RUN_ATTEMPT:?missing GitHub run attempt}"

PG_BIN="${PG_BIN:-/usr/lib/postgresql/17/bin}"
RESTORE_PORT="${RESTORE_PORT:-55432}"
RESTORE_PGDATA="$RUNNER_TEMP/vowpic-restore-pgdata-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
RESTORE_PGLOG="$RUNNER_TEMP/vowpic-restore-postgres-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.log"
RESTORE_ADMIN_PASSWORD="$(openssl rand -hex 32)"
RESTORE_ROLE_PASSWORD="$(openssl rand -hex 32)"
RESTORE_TARGET_ROLE_NAME="vowpic_restore_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}"
RESTORE_TARGET_DATABASE_NAME="$RESTORE_TARGET_ROLE_NAME"
RESTORE_TARGET_CREDENTIAL_EXPIRES_AT="$(date -u -d '+90 minutes' '+%Y-%m-%dT%H:%M:%SZ')"

if [[ ! "$RESTORE_PORT" =~ ^[0-9]+$ ]] ||
   (( RESTORE_PORT < 1024 || RESTORE_PORT > 65535 )); then
  echo "invalid isolated restore port" >&2
  exit 1
fi
if [[ ! "$GITHUB_RUN_ID" =~ ^[0-9]+$ ]] ||
   [[ ! "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]]; then
  echo "invalid GitHub run coordinates" >&2
  exit 1
fi

RUNNER_TEMP_RESOLVED="$(realpath -m "$RUNNER_TEMP")"
WORKSPACE_ARTIFACTS_RESOLVED="$(realpath -m "$GITHUB_WORKSPACE/artifacts")"
RESTORE_PGDATA="$(realpath -m "$RESTORE_PGDATA")"
RESTORE_PGLOG="$(realpath -m "$RESTORE_PGLOG")"
RESTORE_SCRATCH_DIR="$(realpath -m "$RESTORE_SCRATCH_DIR")"
RESTORE_ARTIFACT_DIR="$(realpath -m "$RESTORE_ARTIFACT_DIR")"
case "$RESTORE_PGDATA" in
  "$RUNNER_TEMP_RESOLVED"/*) ;;
  *) echo "restore PGDATA must be below runner temp" >&2; exit 1 ;;
esac
case "$RESTORE_PGLOG" in
  "$RUNNER_TEMP_RESOLVED"/*) ;;
  *) echo "restore log must be below runner temp" >&2; exit 1 ;;
esac
case "$RESTORE_SCRATCH_DIR" in
  "$RUNNER_TEMP_RESOLVED"/*) ;;
  *) echo "restore scratch directory must be below runner temp" >&2; exit 1 ;;
esac
case "$RESTORE_ARTIFACT_DIR" in
  "$WORKSPACE_ARTIFACTS_RESOLVED"/*) ;;
  *) echo "restore artifact directory must be below workspace artifacts" >&2; exit 1 ;;
esac

cleanup() {
  "$PG_BIN/pg_ctl" -D "$RESTORE_PGDATA" -m immediate -w stop >/dev/null 2>&1 || true
  rm -rf -- "$RESTORE_PGDATA" "$RESTORE_SCRATCH_DIR"
  rm -f -- "$RESTORE_PGLOG"
  unset RESTORE_ADMIN_PASSWORD RESTORE_ROLE_PASSWORD
}
trap cleanup EXIT

rm -rf -- "$RESTORE_PGDATA" "$RESTORE_SCRATCH_DIR"
mkdir -p "$RESTORE_ARTIFACT_DIR" "$RESTORE_SCRATCH_DIR"

"$PG_BIN/initdb" -D "$RESTORE_PGDATA" --username=postgres \
  --auth-host=trust --auth-local=trust --encoding=UTF8 --no-locale
if ! "$PG_BIN/pg_ctl" -D "$RESTORE_PGDATA" -l "$RESTORE_PGLOG" \
  -o "-h 127.0.0.1 -k $RESTORE_PGDATA -p $RESTORE_PORT" -w start; then
  echo "isolated PostgreSQL failed to start; pre-credential server log follows" >&2
  if [[ -s "$RESTORE_PGLOG" ]]; then
    tail -n 80 "$RESTORE_PGLOG" >&2
  else
    echo "isolated PostgreSQL did not create a server log" >&2
  fi
  exit 1
fi
"$PG_BIN/psql" --host 127.0.0.1 --port "$RESTORE_PORT" \
  --username postgres --dbname postgres --set ON_ERROR_STOP=1 <<SQL
ALTER ROLE postgres WITH PASSWORD '$RESTORE_ADMIN_PASSWORD';
CREATE ROLE "$RESTORE_TARGET_ROLE_NAME"
  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD '$RESTORE_ROLE_PASSWORD'
  VALID UNTIL '$RESTORE_TARGET_CREDENTIAL_EXPIRES_AT';
CREATE DATABASE "$RESTORE_TARGET_DATABASE_NAME" OWNER "$RESTORE_TARGET_ROLE_NAME";
SQL
sed -i -E 's/^((local|host)[[:space:]].*)trust$/\1scram-sha-256/' \
  "$RESTORE_PGDATA/pg_hba.conf"
"$PG_BIN/pg_ctl" -D "$RESTORE_PGDATA" reload

export RESTORE_TARGET_DATABASE_URL="postgresql://$RESTORE_TARGET_ROLE_NAME:$RESTORE_ROLE_PASSWORD@127.0.0.1:$RESTORE_PORT/$RESTORE_TARGET_DATABASE_NAME?sslmode=disable"
export RESTORE_TARGET_ADMIN_DATABASE_URL="postgresql://postgres:$RESTORE_ADMIN_PASSWORD@127.0.0.1:$RESTORE_PORT/postgres?sslmode=disable"
export RESTORE_TARGET_ROLE_NAME

python backend/scripts/backup_restore_rehearsal.py \
  --source-url-env PRODUCTION_READ_ONLY_DATABASE_URL \
  --target-url-env RESTORE_TARGET_DATABASE_URL \
  --target-admin-url-env RESTORE_TARGET_ADMIN_DATABASE_URL \
  --target-role-name-env RESTORE_TARGET_ROLE_NAME \
  --artifact-dir "$RESTORE_ARTIFACT_DIR" \
  --scratch-dir "$RESTORE_SCRATCH_DIR"
