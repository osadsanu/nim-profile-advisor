# Database export

Snapshot of the MongoDB database `nim_advisor` created with `docker compose run --rm db-export`
(see the [README](../README.md#database-export)). `export_info.json` records when it was taken, the MongoDB version,
the import run it contains and the document count of every collection.

- `mongodump/nim_advisor/` - restore with `docker compose run --rm db-restore`
  or `mongorestore --gzip --drop --dir db_export/mongodump --uri <mongodb-uri>`
- `json/` - one document per line (MongoDB relaxed Extended JSON). Read the gzipped files with e.g.
  `gzip -dc json/profiles.json.gz | jq -c 'select(.model_id == "llama-3.3-70b-instruct_latest") | {profile_id, precision, tp}'`
