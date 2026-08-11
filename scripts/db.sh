#!/usr/bin/env bash
# Local Postgres control for the claim store.
#
# NATIVE arm64 via conda-forge, deliberately NOT Homebrew. The original trigger was
# that brew here was a migrated Intel install at /usr/local running under Rosetta, so
# everything it built was x86_64 translated. That is history: the Intel brew was
# cleaned up 2026-07-28 and brew is now native arm64 at /opt/homebrew. The choice
# stands on its own merits regardless -- conda's subdir here is osx-arm64, it needs no
# sudo, and it removes cleanly with `conda env remove -n grapevine-db`.
#
# Port 5433, trust auth. This script connects over the unix socket in /tmp, but
# listen_addresses is left at the Postgres default of `localhost`, so the server ALSO
# answers on 127.0.0.1:5433 and [::1]:5433, with trust in pg_hba.conf for both. That
# TCP path is the one GUI clients use (Postico 2 is configured against it). It is
# loopback only -- no routable interface is bound, so this is not network exposure.
# It does mean any local process can connect as grapevine without a password, which
# is the intended trade for a scratch database on a single-user machine.
#
#   ./scripts/db.sh setup    one-time: create the env
#   ./scripts/db.sh start
#   ./scripts/db.sh stop
#   ./scripts/db.sh status
#   ./scripts/db.sh psql
#   ./scripts/db.sh reset    drop + recreate + reapply schema
#   ./scripts/db.sh apply    (re)apply schema to the existing db
set -euo pipefail

ENV_NAME=grapevine-db
PGBIN="${CONDA_PREFIX_ROOT:-/opt/miniconda3}/envs/$ENV_NAME/bin"
export PGDATA="${GRAPEVINE_PGDATA:-$HOME/.grapevine/pgdata}"
export PGPORT="${GRAPEVINE_PGPORT:-5433}"
PGHOST=/tmp
PGUSER=grapevine
DB=grapevine
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

psql_() { "$PGBIN/psql" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$@"; }

case "${1:-}" in
  setup)
    conda create -y -n "$ENV_NAME" -c conda-forge postgresql=18.4 pgvector
    echo "Verify it is native (must say arm64, not x86_64):"
    file "$PGBIN/postgres"
    ;;
  start)
    mkdir -p "$(dirname "$PGDATA")"
    [ -d "$PGDATA/base" ] || "$PGBIN/initdb" -D "$PGDATA" -U "$PGUSER" --auth=trust -E UTF8
    "$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k $PGHOST" -l "$HOME/.grapevine/pg.log" start
    sleep 1
    psql_ -d postgres -tc "SELECT 1" >/dev/null 2>&1 || { echo "server did not come up; see ~/.grapevine/pg.log"; exit 1; }
    psql_ -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1 \
      || "$PGBIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$DB"
    echo "up on $PGHOST:$PGPORT/$DB"
    ;;
  stop)    "$PGBIN/pg_ctl" -D "$PGDATA" stop ;;
  status)  "$PGBIN/pg_ctl" -D "$PGDATA" status || true ;;
  psql)    shift; psql_ -d "$DB" "$@" ;;
  apply)
    psql_ -d "$DB" -v ON_ERROR_STOP=1 -q -f "$REPO/schema/claim_store.sql"
    psql_ -d "$DB" -v ON_ERROR_STOP=1 -q -f "$REPO/schema/vocabularies.sql"
    echo "schema applied"
    ;;
  reset)
    psql_ -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
    "$PGBIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$DB"
    "$0" apply
    ;;
  # Usage is the header block itself: print from line 2 until the first non-comment
  # line. Deliberately not a fixed line range -- the old '2,20p' silently drifted
  # into printing `set -euo pipefail` as if it were usage text, and would drift again
  # every time this header is edited.
  *) sed -n '2,${/^#/!q;p;}' "${BASH_SOURCE[0]}"; exit 1 ;;
esac
