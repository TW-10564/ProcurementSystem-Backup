#!/usr/bin/env bash
# Refreshes the local ONE-source cache from the real NAS (Z:) via Windows
# interop, then runs a full preserving rebuild + forecast regenerate.
#
# WHY this exists: WSL does not auto-mount net-use'd drive letters like Z:
# the way it auto-mounts fixed disks like C: (drvfs auto-mount only sees
# local Windows disks), and mounting one manually needs root, which this
# environment doesn't have. Z: IS actually connected on the Windows side
# (`net use` shows it OK) and Windows interop (powershell.exe/cmd.exe) DOES
# work from this shell — so the practical workaround is: robocopy-mirror
# the NAS folder onto C: (which IS mounted), point ONE_SOURCE_DIR at that
# local copy, then rebuild. This script is a snapshot-then-use operation,
# NOT a live mount — re-run it before any rebuild where fresh NAS data
# actually matters; the cache goes stale the moment someone else changes
# a file on Z: after this runs.
set -euo pipefail
cd "$(dirname "$0")"

ONE_CACHE_WIN='C:\Users\10532\OneDrive - 株式会社サードウェーブ\Pictures\Camera Roll\Procurement-System\ONE\ONE'
PS_SCRIPT=$(mktemp /tmp/refresh_one_XXXX.txt)
cat > "$PS_SCRIPT" <<EOF
\$dest = "$ONE_CACHE_WIN"
New-Item -ItemType Directory -Force -Path \$dest | Out-Null
robocopy "Z:\\" \$dest /XO /R:1 /W:1 /NDL /NJH | Out-Null
Get-ChildItem \$dest | Measure-Object | Select-Object Count
EOF

ENCODED=$(python3 -c "
import base64
with open('$PS_SCRIPT', encoding='utf-8') as f:
    cmd = f.read()
print(base64.b64encode(cmd.encode('utf-16-le')).decode('ascii'))
")

echo "[1/3] Refreshing local ONE cache from Z: via Windows interop..."
powershell.exe -EncodedCommand "$ENCODED" 2>&1 | grep -v '^$' || true
rm -f "$PS_SCRIPT"

echo "[2/3] Running full preserving rebuild..."
python3 -m onefcst.rebuild_preserving

echo "[3/3] Regenerating forecast..."
python3 -m onefcst.forecasting

echo "Done."
