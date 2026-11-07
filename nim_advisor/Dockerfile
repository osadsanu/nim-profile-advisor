# Build context: the repository root
#   docker build -f nim_advisor/Dockerfile -t nim-advisor:local .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Optional corporate root CAs (e.g. Zscaler): put PEM files named *.crt in nim_advisor/certs
# They are added to the system trust store so TLS stays verified behind the proxy.
COPY nim_advisor/certs/ /usr/local/share/ca-certificates/extra/

# Tools of nim_profile_finder_v2.sh (Load NIMs tab): skopeo, jq, curl, file; zstd for zstd-compressed layers
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl file jq skopeo zstd \
 && rm -rf /var/lib/apt/lists/* \
 && if ls /usr/local/share/ca-certificates/extra/*.crt >/dev/null 2>&1; then update-ca-certificates; fi
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

WORKDIR /srv
COPY nim_advisor/requirements.txt .
RUN pip install -r requirements.txt

# Data bundled for Kubernetes / PCAI (imported at start-up with SEED_IMPORT, or by `python -m app.importer`).
# docker compose mounts the repository copies over it.
COPY nim_catalog.json /data/nim_catalog.json
COPY reference/ /data/reference/
COPY nims_catalog/ /data/nims_catalog/

COPY nim_profile_finder_v2.sh /srv/nim_profile_finder_v2.sh
COPY nim_advisor/app/ app/

RUN groupadd --gid 10001 advisor \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin advisor
USER 10001:10001

ENV DATA_DIR=/data \
    FINDER_SCRIPT=/srv/nim_profile_finder_v2.sh

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]