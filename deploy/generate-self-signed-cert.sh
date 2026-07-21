#!/usr/bin/env bash
# One-time self-signed TLS cert for internal-network-only use (see
# nginx-onefcst.conf). Browsers will show a trust warning on first visit —
# expected for a self-signed cert; add it as an exception, or replace with
# an internal-CA-issued cert if the trust warning isn't acceptable.
set -euo pipefail

HOSTNAME="${1:?Usage: $0 <dgx-hostname-or-ip>}"
OUT_DIR="/etc/onefcst/tls"

sudo mkdir -p "$OUT_DIR"
sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout "$OUT_DIR/onefcst.key" \
    -out "$OUT_DIR/onefcst.crt" \
    -subj "/CN=$HOSTNAME" \
    -addext "subjectAltName=DNS:$HOSTNAME"

sudo chmod 600 "$OUT_DIR/onefcst.key"
echo "Cert written to $OUT_DIR/onefcst.crt (825-day validity — reissue before it expires)"
