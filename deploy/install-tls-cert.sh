#!/usr/bin/env bash
# Install the twave.co.jp TLS certificate for nginx, with the checks that
# actually catch a bad install.
#
#   sudo deploy/install-tls-cert.sh star_twave_co_jp.crt "DigiCertCA 1.crt" newkey.pem
#   sudo deploy/install-tls-cert.sh ... --reload        # also reload nginx
#   deploy/install-tls-cert.sh ... --dest /tmp/try      # dry run, no sudo
#
# Why a script rather than three cp commands: every failure mode here is silent
# in a different way.
#   * fullchain ORDER — leaf first, then intermediate. Reversed, nginx starts
#     fine and some clients (notably older Android and some Java stacks) fail
#     the handshake while your browser is happy.
#   * MISSING intermediate — same story: fine in Chrome on a machine that has
#     cached the issuer, broken elsewhere.
#   * key/cert MISMATCH — nginx refuses to start, which is the good case, but
#     the error names neither file.
#   * key PERMISSIONS — a 0644 private key is a finding, not a nit.
# openssl answers all four before anything is written.
#
# The certificate is a WILDCARD *.twave.co.jp (DigiCert/GeoTrust). It covers
# one-forecast.twave.co.jp and any other single label. It does NOT cover
# deeper names like a.b.twave.co.jp.
set -euo pipefail

CERT=""; CHAIN=""; KEY=""; DEST="/etc/onefcst/tls"; RELOAD=0
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dest)   DEST="$2"; shift 2 ;;
    --reload) RELOAD=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) positional+=("$1"); shift ;;
  esac
done
if [ "${#positional[@]}" -ne 3 ]; then
  echo "usage: $0 <server.crt> <intermediate.crt> <private.key> [--dest DIR] [--reload]" >&2
  exit 2
fi
CERT="${positional[0]}"; CHAIN="${positional[1]}"; KEY="${positional[2]}"

for f in "$CERT" "$CHAIN" "$KEY"; do
  [ -r "$f" ] || { echo "ERROR: cannot read $f" >&2; exit 1; }
done

echo "== certificate =="
openssl x509 -in "$CERT" -noout -subject -issuer -dates
SAN=$(openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' ')
echo "SAN: ${SAN:-<none>}"

# 1. key must belong to this certificate
c_mod=$(openssl x509 -in "$CERT" -noout -modulus | openssl md5)
k_mod=$(openssl pkey -in "$KEY" -pubout -outform der 2>/dev/null \
        | openssl md5 >/dev/null 2>&1 && openssl rsa -in "$KEY" -noout -modulus | openssl md5)
[ "$c_mod" = "$k_mod" ] || { echo "ERROR: private key does not match this certificate" >&2; exit 1; }
echo "key matches certificate: OK"

# 2. chain must verify with the supplied intermediate
openssl verify -untrusted "$CHAIN" "$CERT" >/dev/null \
  || { echo "ERROR: certificate does not verify against $CHAIN" >&2; exit 1; }
echo "chain verifies: OK"

# 3. expiry — 30 days is enough warning to get a renewal through procurement
if ! openssl x509 -in "$CERT" -noout -checkend $((30*24*3600)) >/dev/null; then
  echo "WARNING: expires within 30 days ($(openssl x509 -in "$CERT" -noout -enddate))" >&2
fi

# 4. an encrypted key would make nginx prompt for a passphrase at boot and hang
if grep -q "ENCRYPTED" "$KEY"; then
  echo "ERROR: private key is passphrase-protected. nginx would block on boot." >&2
  echo "       Decrypt it first: openssl rsa -in $KEY -out newkey.decrypted.pem" >&2
  exit 1
fi

install -d -m 755 "$DEST"
umask 077
# Leaf FIRST, then intermediate. Root is deliberately not included — clients
# already trust it, and shipping it only wastes handshake bytes.
cat "$CERT" "$CHAIN" > "$DEST/onefcst.crt.new"
cp "$KEY" "$DEST/onefcst.key.new"
chmod 644 "$DEST/onefcst.crt.new"
chmod 600 "$DEST/onefcst.key.new"
mv "$DEST/onefcst.crt.new" "$DEST/onefcst.crt"
mv "$DEST/onefcst.key.new" "$DEST/onefcst.key"

n=$(grep -c "BEGIN CERTIFICATE" "$DEST/onefcst.crt")
echo "installed $DEST/onefcst.crt ($n certificates, leaf first) and onefcst.key (0600)"
[ "$n" -eq 2 ] || echo "WARNING: expected 2 certificates in the chain, found $n" >&2

if [ "$RELOAD" = 1 ]; then
  nginx -t && systemctl reload nginx && echo "nginx reloaded"
else
  echo "next: nginx -t && systemctl reload nginx"
fi

cat <<'EOF'

Verify from another machine once nginx is serving it:
  echo | openssl s_client -connect one-forecast.twave.co.jp:443 \
      -servername one-forecast.twave.co.jp 2>/dev/null \
      | grep -E "subject=|Verify return code"
Expect "Verify return code: 0 (ok)". Anything else means the chain is wrong,
not the certificate.
EOF
