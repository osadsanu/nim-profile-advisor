#!/usr/bin/env bash

set -euo pipefail

###############################################################################
# NIM Profile Finder
#
# Extracts NIM model profiles without pulling the complete NIM image.
#
# Usage:
#   ./nim_profile_finder.sh <NIM_IMAGE>
#
# Example:
#   ./nim_profile_finder.sh nvcr.io/nim/qwen/qwen3.5-122b-a10b:latest
#
# Requirements:
#   skopeo jq curl base64 gzip tar file
#
# Authentication:
#   Skopeo must already be authenticated to nvcr.io.
#
# Recommended:
#   skopeo login --authfile ~/.config/containers/auth.json nvcr.io
###############################################################################

IMAGE="${1:-}"

if [[ -z "$IMAGE" ]]; then
    echo "Usage:"
    echo "  $0 <NIM_IMAGE>"
    echo
    echo "Example:"
    echo "  $0 nvcr.io/nim/qwen/qwen3.5-122b-a10b:latest"
    exit 1
fi

OUTPUT_DIR="${2:-./nim-profile-output}"

mkdir -p "$OUTPUT_DIR"

TMP_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP_DIR"
}

trap cleanup EXIT

MANIFEST_FILE="$OUTPUT_DIR/model_manifest.yaml"
PROFILE_FILE="$OUTPUT_DIR/nim_profiles.txt"

###############################################################################
# Determine Skopeo auth file
###############################################################################

AUTHFILE=""

if [[ -f "$HOME/.config/containers/auth.json" ]]; then
    AUTHFILE="$HOME/.config/containers/auth.json"
elif [[ -f "$HOME/.docker/config.json" ]]; then
    AUTHFILE="$HOME/.docker/config.json"
fi

if [[ -n "$AUTHFILE" ]]; then
    echo "Using Skopeo auth file:"
    echo "  $AUTHFILE"
else
    echo "WARNING: No authentication file found."
fi

###############################################################################
# Build Skopeo authentication arguments
###############################################################################

SKOPEO_AUTH_ARGS=()

if [[ -n "$AUTHFILE" ]]; then
    SKOPEO_AUTH_ARGS=(--authfile "$AUTHFILE")
fi

###############################################################################
# Test NGC access
###############################################################################

echo
echo "============================================================"
echo "Testing NGC access"
echo "============================================================"

if ! skopeo inspect \
        "${SKOPEO_AUTH_ARGS[@]}" \
        --tls-verify=false "docker://$IMAGE" >/dev/null; then

    echo
    echo "ERROR: Skopeo cannot access:"
    echo "  $IMAGE"
    echo
    echo "Run:"
    echo
    echo "  skopeo login --authfile ~/.config/containers/auth.json nvcr.io"
    echo
    echo "using:"
    echo
    echo "  Username: \$oauthtoken"
    echo "  Password: <NGC API Key>"
    exit 1
fi

echo "NGC access: OK"

###############################################################################
# STEP 1
# Check for model.manifest.yaml image label
###############################################################################

echo
echo "============================================================"
echo "Checking for model.manifest.yaml image label"
echo "============================================================"

LABEL_DATA="$(
    skopeo inspect \
        "${SKOPEO_AUTH_ARGS[@]}" \
        --tls-verify=false "docker://$IMAGE" \
        | jq -r '.Labels["model.manifest.yaml"] // empty'
)"

###############################################################################
# Scenario A:
# Manifest available as an image label
###############################################################################

if [[ -n "$LABEL_DATA" ]]; then

    echo
    echo "Found model.manifest.yaml image label."

    printf '%s\n' "$LABEL_DATA" > "$TMP_DIR/manifest.b64"

    # The label is usually base64 (optionally gzip). Some images use unpadded
    # or URL-safe base64 and a few store plain YAML, so handle those too.
    B64_DATA="$(tr -d '\r\n ' < "$TMP_DIR/manifest.b64")"

    case $(( ${#B64_DATA} % 4 )) in
        2) B64_DATA="${B64_DATA}==" ; echo "Manifest label: adding missing base64 padding" ;;
        3) B64_DATA="${B64_DATA}="  ; echo "Manifest label: adding missing base64 padding" ;;
    esac

    printf '%s\n' "$B64_DATA" > "$TMP_DIR/manifest.b64.padded"

    DECODED=0

    if base64 -d "$TMP_DIR/manifest.b64.padded" > "$TMP_DIR/manifest.decoded" 2>/dev/null; then
        DECODED=1
    elif tr '_-' '/+' < "$TMP_DIR/manifest.b64.padded" \
            | base64 -d > "$TMP_DIR/manifest.decoded" 2>/dev/null; then
        DECODED=1
        echo "Manifest label uses URL-safe base64"
    fi

    if [[ "$DECODED" -eq 1 ]] && file "$TMP_DIR/manifest.decoded" \
        | grep -qi "gzip compressed"; then

        echo "Manifest format: Base64 + gzip"

        gzip -dc \
            "$TMP_DIR/manifest.decoded" \
            > "$MANIFEST_FILE"

    elif [[ "$DECODED" -eq 1 ]]; then

        echo "Manifest format: Base64 + plain YAML"

        cp \
            "$TMP_DIR/manifest.decoded" \
            "$MANIFEST_FILE"

    elif grep -qE '^(schema_version|profiles|model_profile|manifest):' "$TMP_DIR/manifest.b64"; then

        echo "Manifest format: plain YAML label (not base64)"

        cp \
            "$TMP_DIR/manifest.b64" \
            "$MANIFEST_FILE"

    else

        echo
        echo "ERROR: model.manifest.yaml label is neither base64 nor YAML."
        echo "Label starts with:"
        head -c 300 "$TMP_DIR/manifest.b64" | cat -v
        echo
        echo "Label length: $(wc -c < "$TMP_DIR/manifest.b64") bytes"
        echo "Non-base64 characters: $(tr -d 'A-Za-z0-9+/=\n' < "$TMP_DIR/manifest.b64" | head -c 40 | od -An -c | tr -s ' ')"
        echo "'=' positions: $(grep -bo '=' "$TMP_DIR/manifest.b64" | head -5 | cut -d: -f1 | tr '\n' ' ')"
        exit 1
    fi

###############################################################################
# Scenario B:
# Manifest exists inside an image layer
###############################################################################

else

    echo
    echo "model.manifest.yaml label not found."
    echo "Searching image history for manifest layer."

    ###########################################################################
    # Get image configuration
    ###########################################################################

    CONFIG_FILE="$TMP_DIR/config.json"

    skopeo inspect \
        "${SKOPEO_AUTH_ARGS[@]}" \
        --config \
        --tls-verify=false "docker://$IMAGE" \
        > "$CONFIG_FILE"

    ###########################################################################
    # Find history entry containing model_manifest.yaml
    ###########################################################################

###############################################################################
# Find the history entry that actually ADDS model_manifest.yaml
#
# Priority:
#   1. COPY ... model_manifest.yaml
#   2. ADD  ... model_manifest.yaml
#   3. Fallback to any history entry mentioning model_manifest.yaml
#
# This prevents RUN commands that merely reference/symlink the manifest
# from being selected.
###############################################################################

###############################################################################
# All candidates are kept (most likely first) because some COPY steps produce
# an empty layer; the download step below tries them in order.
#
# Fix: some image histories have entries without "created_by" (null), which
# made test() fail - default it to "".
###############################################################################

    HISTORY_CANDIDATES="$(
        {
            jq -r '
                (.history // [])
                | map(select(.empty_layer != true))
                | to_entries[]
                | select(
                    ((.value.created_by // "") | test("(^|[[:space:]])(COPY|ADD)[[:space:]]"; "i"))
                    and
                    ((.value.created_by // "") | test("model_manifest\\.yaml"; "i"))
                  )
                | "\(.key)|\(.value.created_by)"
            ' "$CONFIG_FILE" | tac

###############################################################################
# Fallback candidates: any history entry containing model_manifest.yaml
###############################################################################

            jq -r '
                (.history // [])
                | map(select(.empty_layer != true))
                | to_entries[]
                | select((.value.created_by // "") | test("model_manifest\\.yaml"; "i"))
                | "\(.key)|\(.value.created_by)"
            ' "$CONFIG_FILE" | tac
        } | awk -F'|' '!seen[$1]++'
    )"

    if [[ -z "$HISTORY_CANDIDATES" ]]; then
        echo
        echo "ERROR: Could not find model_manifest.yaml in image history."
        exit 1
    fi

    MODEL_LAYER_INDEX="$(head -1 <<< "$HISTORY_CANDIDATES" | cut -d'|' -f1)"
    HISTORY_COMMAND="$(head -1 <<< "$HISTORY_CANDIDATES" | cut -d'|' -f2-)"

    echo
    echo "Manifest layer found:"
    echo "  Layer index : $MODEL_LAYER_INDEX"
    echo "  Command     : $HISTORY_COMMAND"
    echo "  Candidates  : $(wc -l <<< "$HISTORY_CANDIDATES")"

    ###########################################################################
    # Get raw image/index
    ###########################################################################

    RAW_FILE="$TMP_DIR/raw.json"

    skopeo inspect \
        "${SKOPEO_AUTH_ARGS[@]}" \
        --raw \
        --tls-verify=false "docker://$IMAGE" \
        > "$RAW_FILE"

    ###########################################################################
    # Resolve multi-platform image
    ###########################################################################

    if jq -e '.manifests' "$RAW_FILE" >/dev/null 2>&1; then

        echo
        echo "Multi-platform image detected."

        PLATFORM_DIGEST="$(
            jq -r '
                .manifests[]
                | select(
                    .platform.os == "linux"
                    and .platform.architecture == "amd64"
                  )
                | .digest
            ' "$RAW_FILE" | head -1
        )"

        if [[ -z "$PLATFORM_DIGEST" || "$PLATFORM_DIGEST" == "null" ]]; then
            echo "ERROR: Could not find linux/amd64 image."
            exit 1
        fi

        echo "AMD64 image digest:"
        echo "  $PLATFORM_DIGEST"

        IMAGE_MANIFEST="$TMP_DIR/image-manifest.json"

        # Remove tag/digest from original image and append digest.

	IMAGE_BASE="${IMAGE%@*}"
        IMAGE_BASE="${IMAGE_BASE%:*}"

        echo "Resolving platform-specific image:"
        echo "  docker://${IMAGE_BASE}@${PLATFORM_DIGEST}"

        skopeo inspect \
           "${SKOPEO_AUTH_ARGS[@]}" \
           --raw \
           --tls-verify=false "docker://${IMAGE_BASE}@${PLATFORM_DIGEST}" \
           > "$IMAGE_MANIFEST"

    else

        echo
        echo "Single-platform image detected."

        IMAGE_MANIFEST="$RAW_FILE"

    fi

    ###########################################################################
    # Verify layers
    ###########################################################################

    LAYER_COUNT="$(jq '.layers | length' "$IMAGE_MANIFEST")"

    echo
    echo "Number of image layers: $LAYER_COUNT"

    ###########################################################################
    # Get layer digest
    ###########################################################################

    INSPECT_FILE="$TMP_DIR/inspect.json"

    skopeo inspect \
        "${SKOPEO_AUTH_ARGS[@]}" \
        --override-os linux \
        --override-arch amd64 \
        --tls-verify=false "docker://$IMAGE" \
        > "$INSPECT_FILE"

    # Layer digest for a history (non-empty layer) index
    layer_digest() {
        jq -r \
            --argjson i "$1" \
            '.Layers[$i] // empty' \
            "$INSPECT_FILE"
    }

    MODEL_LAYER_DIGEST="$(layer_digest "$MODEL_LAYER_INDEX")"

    if [[ -z "$MODEL_LAYER_DIGEST" ]]; then
        echo
        echo "ERROR: Could not determine manifest layer digest."
        exit 1
    fi

    echo
    echo "Manifest layer:"
    echo "  $MODEL_LAYER_DIGEST"

    ###########################################################################
    # NGC repository
    ###########################################################################

    IMAGE_PATH="${IMAGE#nvcr.io/}"

    # Remove digest
    IMAGE_PATH="${IMAGE_PATH%@*}"

    # Remove tag
    REPOSITORY="${IMAGE_PATH%:*}"

    echo
    echo "NGC repository:"
    echo "  $REPOSITORY"

    ###########################################################################
    # Get NGC credentials
    #
    # We use Skopeo auth file credentials only when directly available.
    ###########################################################################

    DOCKER_CONFIG="$HOME/.docker/config.json"
    CONTAINERS_AUTH="$HOME/.config/containers/auth.json"

    NGC_USER=""
    NGC_PASS=""

    for AUTH_FILE in "$CONTAINERS_AUTH" "$DOCKER_CONFIG"; do

        if [[ -f "$AUTH_FILE" ]]; then

            AUTH="$(
                jq -r \
                    '.auths["nvcr.io"].auth // empty' \
                    "$AUTH_FILE" 2>/dev/null || true
            )"

            if [[ -n "$AUTH" ]]; then

                CREDS="$(printf '%s' "$AUTH" | base64 -d)"

                NGC_USER="${CREDS%%:*}"
                NGC_PASS="${CREDS#*:}"

                break
            fi
        fi
    done

    if [[ -z "$NGC_USER" || -z "$NGC_PASS" ]]; then

        echo
        echo "ERROR: Could not obtain NGC credentials for blob download."
        echo
        echo "Run:"
        echo
        echo "  skopeo login --authfile ~/.config/containers/auth.json nvcr.io"
        echo
        echo "Then rerun this script."
        exit 1
    fi

    ###########################################################################
    # Get NGC bearer token
    ###########################################################################

    echo
    echo "Obtaining NGC bearer token."

    TOKEN_RESPONSE="$(
        curl -k -fsS \
            -u "$NGC_USER:$NGC_PASS" \
            "https://nvcr.io/proxy_auth?scope=repository:${REPOSITORY}:pull"
    )"

    TOKEN="$(
        printf '%s' "$TOKEN_RESPONSE" \
            | jq -r '.token // empty'
    )"

    if [[ -z "$TOKEN" ]]; then
        echo
        echo "ERROR: Could not obtain NGC bearer token."
        exit 1
    fi

    echo "NGC bearer token: OK"

    ###########################################################################
    # Download ONLY manifest layer
    ###########################################################################

    LAYER_FILE="$TMP_DIR/manifest-layer"
    MANIFEST_PATH=""

    # Try every candidate layer until model_manifest.yaml is found
    # (a COPY step can produce an empty layer).
    while IFS='|' read -r CANDIDATE_INDEX CANDIDATE_COMMAND; do

    MODEL_LAYER_DIGEST="$(layer_digest "$CANDIDATE_INDEX")"

    if [[ -z "$MODEL_LAYER_DIGEST" ]]; then
        echo "Skipping candidate $CANDIDATE_INDEX: no layer digest."
        continue
    fi

    echo
    echo "============================================================"
    echo "Downloading ONLY manifest layer"
    echo "============================================================"

    echo "Layer index: $CANDIDATE_INDEX"
    echo "Command    : $CANDIDATE_COMMAND"
    echo "Layer:"
    echo "  $MODEL_LAYER_DIGEST"

    jq -r \
        --arg digest "$MODEL_LAYER_DIGEST" '
        .layers[]
        | select(.digest == $digest)
        | "Size     : \(.size) bytes\nMediaType: \(.mediaType)"
    ' "$IMAGE_MANIFEST"

    curl -k -fL \
        -H "Authorization: Bearer $TOKEN" \
        "https://nvcr.io/v2/${REPOSITORY}/blobs/${MODEL_LAYER_DIGEST}" \
        -o "$LAYER_FILE" < /dev/null

    echo
    echo "Downloaded:"
    ls -lh "$LAYER_FILE"

    ###########################################################################
    # Extract manifest
    ###########################################################################

#    echo
#    echo "Extracting model_manifest.yaml."

#    MANIFEST_PATH="opt/nim/etc/default/model_manifest.yaml"

#    if ! tar -tf "$LAYER_FILE" \
#        "$MANIFEST_PATH" >/dev/null 2>&1; then

#        echo
#        echo "ERROR: Expected manifest was not found:"
#        echo "  $MANIFEST_PATH"
#        echo
#        echo "Searching layer:"
#        tar -tf "$LAYER_FILE" | grep -i "model_manifest" || true
#        exit 1
#    fi

#    tar -xf "$LAYER_FILE" \
#        -C "$TMP_DIR" \
#        "$MANIFEST_PATH"

#    cp \
#        "$TMP_DIR/$MANIFEST_PATH" \
#        "$MANIFEST_FILE"

    ###############################################################################
    # Locate model_manifest.yaml inside the downloaded layer
    ###############################################################################
    
    # tar -t auto-detects gzip/zstd layers
    MANIFEST_PATH="$(tar -tf "$LAYER_FILE" 2>/dev/null | grep -E '(^|/)model_manifest\.yaml$' | head -1 || true)"

    if [[ -n "$MANIFEST_PATH" ]]; then
        break
    fi

    echo
    echo "model_manifest.yaml is not in this layer; trying the next candidate."

    done <<< "$HISTORY_CANDIDATES"

    if [[ -z "$MANIFEST_PATH" ]]; then

        echo
        echo "ERROR: model_manifest.yaml was not found in any candidate layer."
        echo
        echo "Files in the last layer containing 'manifest':"

        tar -tf "$LAYER_FILE" 2>/dev/null \
            | grep -i "manifest" \
            || true

        exit 1
    fi
    
    echo
    echo "Manifest found inside layer:"
    echo "  $MANIFEST_PATH"
    
    ###############################################################################
    # Extract model_manifest.yaml
    ###############################################################################
    
    tar -xf "$LAYER_FILE" \
        -C "$TMP_DIR" \
        "$MANIFEST_PATH"
    
    cp \
        "$TMP_DIR/$MANIFEST_PATH" \
        "$MANIFEST_FILE"

fi

###############################################################################
# Extract profiles
###############################################################################

if [[ ! -s "$MANIFEST_FILE" ]]; then
    echo
    echo "ERROR: model_manifest.yaml was not created."
    exit 1
fi

#echo
#echo "============================================================"
#echo "NIM PROFILES"
#echo "============================================================"

#grep -A 10 '^- id:' "$MANIFEST_FILE" \
#    | grep -v '^--$' \
#    | tee "$PROFILE_FILE"

#PROFILE_COUNT="$(
#    grep -c '^- id:' "$MANIFEST_FILE" || true
#)"

#echo
#echo "============================================================"
#echo "SUMMARY"
#echo "============================================================"

#echo "Image:"
#echo "  $IMAGE"

#echo
#echo "Manifest:"
#echo "  $MANIFEST_FILE"

#echo
#echo "Profiles:"
#echo "  $PROFILE_COUNT"

#echo
#echo "Output:"
#echo "  $PROFILE_FILE"

#echo
#echo "Completed successfully."

###############################################################################
# Extract NIM profiles
###############################################################################

echo
echo "============================================================"
echo "NIM PROFILES"
echo "============================================================"

: > "$PROFILE_FILE"

###############################################################################
# Format 1:
# Profile ID explicitly specified as:
#
# - id: <64-character UUID>
###############################################################################

EXPLICIT_COUNT=$(grep -cE '^[[:space:]]*-[[:space:]]+id:[[:space:]]*[0-9a-fA-F]{64}[[:space:]]*$' "$MANIFEST_FILE" || true)

if [[ "$EXPLICIT_COUNT" -gt 0 ]]; then

    echo "Profile format: explicit id"

    awk '
        /^[[:space:]]*-[[:space:]]+id:[[:space:]]*[0-9a-fA-F]{64}[[:space:]]*$/ {
            if (found) {
                print ""
            }

            found=1
            in_workspace=0
        }

        found && /^[[:space:]]*workspace:[[:space:]]*$/ {
            in_workspace=1
            next
        }

        found && !in_workspace {
            print
        }
    ' "$MANIFEST_FILE" | tee "$PROFILE_FILE"

fi

###############################################################################
# Format 2:
# Profile ID is a top-level 64-character hexadecimal YAML key:
#
# <UUID>:
#   model: ...
#   release: ...
#   tags:
#     ...
#   workspace:
#     ...
###############################################################################

TOP_LEVEL_COUNT=$(grep -cE '^[0-9a-fA-F]{64}:[[:space:]]*$' "$MANIFEST_FILE" || true)

if [[ "$TOP_LEVEL_COUNT" -gt 0 ]]; then

    echo "Profile format: UUID as YAML key"

    awk '
        /^[0-9a-fA-F]{64}:[[:space:]]*$/ {

            if (found) {
                print ""
            }

            found=1
            in_workspace=0

            print
            next
        }

        found && /^[[:space:]]+workspace:[[:space:]]*$/ {
            in_workspace=1
            next
        }

        found && !in_workspace {
            print
        }
    ' "$MANIFEST_FILE" | tee "$PROFILE_FILE"

fi

###############################################################################
# Count profiles
###############################################################################

PROFILE_COUNT=$((EXPLICIT_COUNT + TOP_LEVEL_COUNT))

if [[ "$PROFILE_COUNT" -eq 0 ]]; then

    echo
    echo "ERROR: No NIM profile IDs found in:"
    echo "  $MANIFEST_FILE"

    exit 1

fi

echo
echo "Profiles found: $PROFILE_COUNT"

echo
echo "Profile output saved to:"
echo "  $PROFILE_FILE"
