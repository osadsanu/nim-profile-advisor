#!/usr/bin/env bash
# Export the NIM Profile Advisor database into ./db_export
#   docker compose run --rm db-export
# Runs inside the mongo:8.0 image (mongodump, mongoexport and mongosh are included).
#
#   db_export/mongodump/<db>/*.bson.gz   full dump incl. indexes and validators (restore: db-restore)
#   db_export/json/<collection>.json     one document per line, MongoDB relaxed Extended JSON;
#                                        files larger than JSON_GZIP_MB (default 10) are gzipped
#   db_export/export_info.json           document counts, source import run, file sizes
set -euo pipefail

: "${MONGO_URI:?MONGO_URI is not set}"
OUT="${EXPORT_DIR:-/export}"
DB="${MONGO_DB:-nim_advisor}"
GZIP_BYTES=$(( ${JSON_GZIP_MB:-10} * 1024 * 1024 ))

# Build the export on the container filesystem and copy it out at the end: many small writes to a
# bind-mounted host folder are very slow on Docker Desktop (Windows / macOS).
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/json"

echo "mongodump $DB -> mongodump/$DB"
mongodump --uri "$MONGO_URI" --db "$DB" --gzip --out "$WORK/mongodump" --quiet

collections=$(mongosh "$MONGO_URI" --quiet --eval "db.getSiblingDB('$DB').getCollectionNames().sort().join(' ')")
for c in $collections; do
    file="$WORK/json/$c.json"
    mongoexport --uri "$MONGO_URI" --db "$DB" --collection "$c" --sort '{"_id": 1}' --out "$file" --quiet
    if (( $(stat -c %s "$file") > GZIP_BYTES )); then
        gzip -9 -n -f "$file"
        file="$file.gz"
    fi
    echo "mongoexport $c -> ${file#"$WORK/"}"
done

DB="$DB" OUT="$WORK" mongosh "$MONGO_URI" --quiet --eval '
const fs = require("fs");
const dbName = process.env.DB, out = process.env.OUT;
const d = db.getSiblingDB(dbName);
const run = d.import_runs.find().sort({ started_at: -1 }).limit(1).toArray()[0] || null;
const list = (dir) => fs.readdirSync(`${out}/${dir}`).sort()
  .map((f) => ({ file: `${dir}/${f}`, bytes: fs.statSync(`${out}/${dir}/${f}`).size }));
print(JSON.stringify({
  exported_at: new Date().toISOString(),
  database: dbName,
  mongodb_version: db.version(),
  import_run: run && { id: run._id, started_at: run.started_at, finished_at: run.finished_at, counts: run.counts },
  collections: Object.fromEntries(d.getCollectionNames().sort().map((c) => [c, d.getCollection(c).countDocuments()])),
  files: [...list(`mongodump/${dbName}`), ...list("json")],
  restore: "docker compose run --rm db-restore",
}, null, 2));
' > "$WORK/export_info.json"

rm -rf "$OUT/mongodump" "$OUT/json"
cp -r "$WORK/mongodump" "$WORK/json" "$WORK/export_info.json" "$OUT/"
echo "wrote mongodump/, json/ and export_info.json to the export folder"
