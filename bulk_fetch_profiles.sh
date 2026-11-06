#!/usr/bin/env bash
###############################################################################
# Bulk NIM Profile Fetcher
#
# Runs nim_profile_finder_v2.sh for every entry of nim_catalog.json and stores
# the result of each model in its own folder:
#
#   nims_catalog/<repo>_<tag>/model_manifest.yaml
#   nims_catalog/<repo>_<tag>/nim_profiles.txt
#
# Folder names: the ':' between repository and tag is replaced by '_' so the
# names are valid on Windows and easy to type:
#
#   nvcr.io/nim/meta/llama-3.1-8b-instruct:latest -> llama-3.1-8b-instruct_latest
#
# Catalog entries that point to a private registry mirror of nvcr.io
# (<INTERNAL_REGISTRY_PREFIX>nvcr.io/..., default prefix
# registry.example.com/ezmeral-common/) are fetched from the equivalent
# nvcr.io image (the finder downloads blobs from nvcr.io) and their folder
# gets the suffix "_<last segment of the prefix>" ("_ezmeral-common") so they
# never collide with the nvcr.io entry of the same repository/tag.
#
# Logs and a status table are written to:
#
#   nims_catalog/_logs/<folder>.log
#   nims_catalog/_fetch_status.tsv
#
# Usage (run inside Linux / WSL):
#   ./bulk_fetch_profiles.sh [catalog.json] [output_dir]
#
# Environment variables:
#   JOBS=6             parallel workers
#   FORCE=1            re-fetch entries that already have both files
#   ONLY=<regex>       only process images matching this regex (testing)
#   MAX_FILE_MB=1024   safety cap: abort any single file bigger than this
#                      (protects the disk if a manifest layer is huge)
#   INTERNAL_REGISTRY_PREFIX=registry.example.com/ezmeral-common/
#                      registry prefix of catalog entries that mirror nvcr.io
#                      (must match the importer setting in docker-compose.yml)
###############################################################################

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CATALOG="${1:-$SCRIPT_DIR/nim_catalog.json}"
OUT_ROOT="${2:-$SCRIPT_DIR/nims_catalog}"
FINDER="$SCRIPT_DIR/nim_profile_finder_v2.sh"

JOBS="${JOBS:-6}"
FORCE="${FORCE:-0}"
ONLY="${ONLY:-}"
MAX_FILE_MB="${MAX_FILE_MB:-1024}"

INTERNAL_PREFIX="${INTERNAL_REGISTRY_PREFIX:-registry.example.com/ezmeral-common/}"
INTERNAL_PREFIX="${INTERNAL_PREFIX%/}/"
MIRROR_SUFFIX="${INTERNAL_PREFIX%/}"
MIRROR_SUFFIX="_${MIRROR_SUFFIX##*/}"

LOG_DIR="$OUT_ROOT/_logs"
STATUS_DIR="$LOG_DIR/.status"
STATUS_FILE="$OUT_ROOT/_fetch_status.tsv"

for tool in jq skopeo curl file xargs; do
    command -v "$tool" >/dev/null || { echo "ERROR: missing tool: $tool"; exit 1; }
done

[[ -f "$CATALOG" ]] || { echo "ERROR: catalog not found: $CATALOG"; exit 1; }
[[ -f "$FINDER"  ]] || { echo "ERROR: finder not found: $FINDER"; exit 1; }

mkdir -p "$OUT_ROOT" "$LOG_DIR" "$STATUS_DIR"

export OUT_ROOT LOG_DIR STATUS_DIR FINDER FORCE MAX_FILE_MB INTERNAL_PREFIX MIRROR_SUFFIX

###############################################################################
# Fetch one catalog entry
#
# Arguments: <catalog name> <catalog image>
# Writes one status line to $STATUS_DIR/<folder>.tsv:
#   folder  name  image  fetched_image  status  profiles  seconds  message
###############################################################################

fetch_one() {
    local name="$1"
    local image="$2"

    local fetch_image="$image"
    local suffix=""

    if [[ "$image" == "$INTERNAL_PREFIX"* ]]; then
        fetch_image="${image#"$INTERNAL_PREFIX"}"
        suffix="$MIRROR_SUFFIX"
    fi

    # Last path component is "<repo>[:<tag>]"
    local last="${fetch_image##*/}"
    local repo tag

    if [[ "$last" == *:* ]]; then
        repo="${last%%:*}"
        tag="${last#*:}"
    else
        # No tag in the catalog -> registries default to "latest"
        repo="$last"
        tag="latest"
        fetch_image="${fetch_image}:latest"
    fi

    # Keep folder names portable: only [A-Za-z0-9._-]
    local folder
    folder="$(printf '%s' "${repo}_${tag}${suffix}" | tr -c 'A-Za-z0-9._-' '_')"

    local outdir="$OUT_ROOT/$folder"
    local log="$LOG_DIR/$folder.log"
    local status_line="$STATUS_DIR/$folder.tsv"

    if [[ "$FORCE" != "1" && -s "$outdir/model_manifest.yaml" && -s "$outdir/nim_profiles.txt" ]]; then
        local cached
        cached="$(grep -cE '^[[:space:]]*-[[:space:]]+id:|^[0-9a-fA-F]{64}:' "$outdir/nim_profiles.txt" || true)"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$folder" "$name" "$image" "$fetch_image" "skipped" "$cached" "0" "already downloaded" \
            > "$status_line"
        echo "[skip] $folder"
        return 0
    fi

    local start=$SECONDS
    local rc=0
    local attempt

    for attempt in 1 2 3; do
        rc=0
        (
            # Safety cap for any single file written by the finder (KB blocks)
            ulimit -f $(( MAX_FILE_MB * 1024 ))
            bash "$FINDER" "$fetch_image" "$outdir"
        ) > "$log" 2>&1 < /dev/null || rc=$?

        [[ $rc -eq 0 ]] && break

        # Retry only transient registry / network problems
        if grep -qiE 'timeout|timed out|connection reset|unexpected EOF|TLS handshake|too many requests|429|502|503|504|temporary failure' "$log"; then
            sleep $(( attempt * 10 ))
            continue
        fi
        break
    done

    local seconds=$(( SECONDS - start ))
    local profiles
    profiles="$(grep -oE 'Profiles found: [0-9]+' "$log" | tail -1 | grep -oE '[0-9]+' || true)"

    local status message=""

    if [[ $rc -eq 0 ]]; then
        status="ok"
    else
        status="failed"
        if [[ $rc -eq 153 ]] || grep -qi 'file size limit' "$log"; then
            message="layer larger than ${MAX_FILE_MB} MB (aborted by safety cap)"
        else
            message="$(grep -E 'ERROR|FATA|Error|error' "$log" | head -2 | tr '\t\n' '  ' | cut -c1-300)"
        fi
        [[ -z "$message" ]] && message="exit code $rc (see _logs/$folder.log)"
    fi

    # Do not leave empty folders behind for failed entries
    if [[ -d "$outdir" ]] && [[ -z "$(ls -A "$outdir" 2>/dev/null)" ]]; then
        rmdir "$outdir" 2>/dev/null || true
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$folder" "$name" "$image" "$fetch_image" "$status" "${profiles:-0}" "$seconds" "$message" \
        > "$status_line"

    printf '[%-6s] %-60s profiles=%-4s %4ss %s\n' "$status" "$folder" "${profiles:-0}" "$seconds" "$message"
}

export -f fetch_one

###############################################################################
# Run all entries in parallel
###############################################################################

TOTAL="$(jq 'length' "$CATALOG")"

echo "Catalog : $CATALOG ($TOTAL entries)"
echo "Output  : $OUT_ROOT"
echo "Workers : $JOBS"
[[ -n "$ONLY" ]] && echo "Filter  : $ONLY"
echo

jq -r '.[] | [.name, .image] | @tsv' "$CATALOG" \
    | { if [[ -n "$ONLY" ]]; then grep -E "$ONLY" || true; else cat; fi; } \
    | tr '\t\n' '\0\0' \
    | xargs -0 -n 2 -P "$JOBS" bash -c 'fetch_one "$1" "$2"' _

###############################################################################
# Merge status lines into one table
###############################################################################

{
    printf 'folder\tname\timage\tfetched_image\tstatus\tprofiles\tseconds\tmessage\n'
    cat "$STATUS_DIR"/*.tsv 2>/dev/null | sort
} > "$STATUS_FILE"

echo
echo "============================================================"
echo "SUMMARY"
echo "============================================================"
awk -F'\t' 'NR > 1 { n[$5]++; p += $6 } END { for (s in n) printf "  %-8s %d\n", s, n[s]; printf "  profiles %d\n", p }' "$STATUS_FILE"
echo
echo "Status table: $STATUS_FILE"
