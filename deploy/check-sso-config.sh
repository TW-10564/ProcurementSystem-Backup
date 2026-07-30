#!/usr/bin/env bash
# Validate the ENTRA_* values BEFORE starting oauth2-proxy.
#
#   deploy/check-sso-config.sh /srv/onefcst/.env
#
# Why: every mistake here surfaces as the same thing — users bounced at
# sign-in — and the proxy's own error is usually a generic OIDC failure that
# names none of the four possible causes. These checks separate them:
#   * a GUID typo'd or swapped (tenant <-> client) -> issuer discovery 404s
#   * the Secret ID pasted instead of the secret VALUE (easy: they sit in
#     adjacent columns, and the ID is also a GUID, so it LOOKS right)
#   * a cookie secret that is not exactly 16/24/32 bytes -> proxy won't start
#   * a redirect URI that differs from the registration by a slash or scheme
#
# Reads values only to check their SHAPE. Nothing is printed in full: secrets
# are shown as a length and a 4-character prefix.
set -uo pipefail

ENV_FILE="${1:-/srv/onefcst/.env}"
[ -r "$ENV_FILE" ] || { echo "cannot read $ENV_FILE" >&2; exit 1; }

get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"'"'"'\r'; }

TENANT=$(get ENTRA_TENANT_ID)
CLIENT=$(get ENTRA_CLIENT_ID)
SECRET=$(get ENTRA_CLIENT_SECRET)
REDIR=$(get ENTRA_REDIRECT_URI)
COOKIE=$(get ENTRA_COOKIE_SECRET)

GUID='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
fail=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  WARN %s\n' "$1"; }

echo "checking $ENV_FILE"

[[ "$TENANT" =~ $GUID ]] && ok "ENTRA_TENANT_ID looks like a GUID" \
  || bad "ENTRA_TENANT_ID is not a GUID (got '${TENANT:-<empty>}')"

[[ "$CLIENT" =~ $GUID ]] && ok "ENTRA_CLIENT_ID looks like a GUID" \
  || bad "ENTRA_CLIENT_ID is not a GUID (got '${CLIENT:-<empty>}')"

[ -n "$TENANT" ] && [ "$TENANT" = "$CLIENT" ] \
  && bad "tenant and client ID are identical — one of them is pasted wrong"

if [ -z "$SECRET" ]; then
  warn "ENTRA_CLIENT_SECRET is empty — fill it in before starting the overlay"
elif [[ "$SECRET" =~ $GUID ]]; then
  bad "ENTRA_CLIENT_SECRET is a GUID — that is the Secret ID, not the VALUE.
       Entra shows the Value once, in the column beside it. If it is gone,
       create a new secret; the old value cannot be retrieved."
else
  ok "ENTRA_CLIENT_SECRET set (${#SECRET} chars, starts '${SECRET:0:4}…')"
fi

case "$REDIR" in
  https://*/oauth2/callback) ok "ENTRA_REDIRECT_URI shape is right" ;;
  http://*)  bad "ENTRA_REDIRECT_URI must be https" ;;
  *)         bad "ENTRA_REDIRECT_URI should end in /oauth2/callback (got '${REDIR:-<empty>}')" ;;
esac
[ -n "$REDIR" ] && echo "       register EXACTLY this in Entra: $REDIR"

case "${#COOKIE}" in
  16|24|32) ok "ENTRA_COOKIE_SECRET length ${#COOKIE} accepted" ;;
  0)  warn "ENTRA_COOKIE_SECRET empty — openssl rand -base64 32 | head -c 32" ;;
  *)  bad "ENTRA_COOKIE_SECRET is ${#COOKIE} bytes; must be exactly 16, 24 or 32" ;;
esac

# Live check: the issuer must be reachable and must echo back our tenant.
if [ -n "$TENANT" ] && command -v curl >/dev/null; then
  url="https://login.microsoftonline.com/$TENANT/v2.0/.well-known/openid-configuration"
  if body=$(curl -fsS -m 10 "$url" 2>/dev/null); then
    echo "$body" | grep -q "$TENANT" \
      && ok "issuer discovery reachable and matches the tenant" \
      || bad "issuer discovery answered but does not mention this tenant"
  else
    warn "issuer discovery unreachable ($url) — no outbound internet from here?
       oauth2-proxy needs to reach login.microsoftonline.com at runtime."
  fi
fi

echo
[ "$fail" = 0 ] && echo "config looks startable" \
  || { echo "fix the FAIL lines before starting the overlay"; exit 1; }
