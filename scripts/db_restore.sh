#!/usr/bin/env bash
# Restore ./db_export/mongodump into MongoDB (existing collections are replaced)
#   docker compose run --rm db-restore
#   docker compose run --rm -e RESTORE_DB=<name> db-restore    # restore into another database
# Runs inside the mongo:8.0 image.
set -euo pipefail

: "${MONGO_URI:?MONGO_URI is not set}"
IN="${EXPORT_DIR:-/export}/mongodump"

src_dir=$(find "$IN" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n 1)
[[ -n "$src_dir" ]] || { echo "ERROR: no dump in $IN (run: docker compose run --rm db-export)"; exit 1; }
SRC_DB=$(basename "$src_dir")
TARGET_DB="${RESTORE_DB:-${MONGO_DB:-$SRC_DB}}"

# Read the dump from a local copy (bind mounts are slow on Docker Desktop)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp -r "$IN" "$WORK/"

echo "mongorestore mongodump/$SRC_DB -> $TARGET_DB"
mongorestore --uri "$MONGO_URI" --gzip --drop --dir "$WORK/mongodump" \
    --nsInclude "$SRC_DB.*" --nsFrom "$SRC_DB.*" --nsTo "$TARGET_DB.*" --quiet

TARGET_DB="$TARGET_DB" mongosh "$MONGO_URI" --quiet --eval '
const d = db.getSiblingDB(process.env.TARGET_DB);
d.getCollectionNames().sort().forEach((c) => print(`${c}: ${d.getCollection(c).countDocuments()}`));
'
