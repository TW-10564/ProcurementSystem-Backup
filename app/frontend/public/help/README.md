# Demo video files / デモ動画ファイル

The **How to Use / 使い方ガイド** page (`/app/help`) plays a video from this
folder. Nothing else needs to change — drop the file in and reload the page.
No rebuild, no server restart.

## Filenames

| File | Used when |
|---|---|
| `demo-ja.mp4` / `demo-ja.webm` | UI language is 日本語 |
| `demo-en.mp4` / `demo-en.webm` | UI language is EN |
| `demo.mp4` / `demo.webm` | Fallback — played in **both** languages when the per-language file above is missing |

Both **MP4** and **WebM** are accepted. The page tries, in order:
`demo-<lang>.mp4`, `demo-<lang>.webm`, `demo.mp4`, `demo.webm`. If none exist it
shows an instructions panel naming these paths, so the tab is never broken —
it just tells the team the video hasn't been recorded yet.

If you only have one recording (likely at first — a single Japanese
walkthrough for both teams), name it `demo.mp4`.

## Recommended encoding

- **Container/codec:** MP4 (H.264 + AAC) or WebM (VP8/VP9) — both play in
  Edge, Chrome and Firefox with no plugin. `.mov` and `.avi` will *not* play
  reliably; convert them first.
- **Resolution:** 1920×1080.
- **Size:** under ~200 MB. These files are served from the web server, so a
  multi-GB recording will be slow to start over the office network.

To convert an existing recording:

```bash
ffmpeg -i recording.mov -c:v libx264 -crf 24 -preset slow -c:a aac -b:a 128k demo-ja.mp4
```

## Note on version control

Video files here **are committed** — the training video is meant to live
inside the system, so the team watches it in the portal instead of passing a
copy around. `.gitignore` deliberately does *not* exclude them.

Because of that, keep the file small (see the size guidance above). Git
stores every version of a binary forever, so re-recording a 500 MB video a
few times will bloat the repository permanently. Replace the file in place
rather than adding `demo-v2.mp4`, `demo-final.mp4`, and so on.

## Playback restrictions

The page plays the video watch-only: no download link, the download button
removed from the player's control bar, and the right-click menu suppressed.

**This is a deterrent, not protection.** The file is still served over plain
HTTP, so anyone who opens the browser's developer tools can save it. If the
content genuinely must not leave the portal, that needs a different approach
(authenticated streaming endpoint with signed, expiring URLs) — ask before
assuming the current setup provides it.

---

## Re-recording the walkthrough (`tools/record_demo.py`)

The current `demo-ja.webm` / `demo-en.webm` are **generated**, not hand-made.
One command re-records either language end to end, so the video stays current
instead of going stale the first time a screen changes:

```bash
python3 tools/record_demo.py --lang ja      # or: en / both
python3 tools/record_demo.py --lang ja --headed   # watch it, when a selector breaks
```

It drives the real app in Chromium via Playwright and adds two things the app
does not have: a **visible cursor** (Playwright's synthetic input moves no
on-screen pointer, so a plain capture looks like the UI operating itself) and
**burnt-in bilingual captions** (narration would need a voice track; captions
also work on a muted office PC).

### It needs its own servers

The normal dev stack has SSO on, so every API call needs a real Microsoft token
and a headless browser cannot get one. Start an SSO-off pair first, pointed at
a **copy** of the database — a recording that types into forms must never be
able to leave an adjustment or an audit-log entry in the real one:

```bash
cp app/data/onefcst.db /tmp/demo-copy.db

# backend on 8654, no SSO, no API key, copy of the DB
cd app/backend && DATABASE_URL="sqlite:////tmp/demo-copy.db" \
  ONEFCST_API_KEY="" ONEFCST_ENTRA_TENANT_ID="" ONEFCST_ENTRA_CLIENT_ID="" \
  ONEFCST_CORS_ORIGINS="http://127.0.0.1:5200" \
  python3 -m uvicorn onefcst.api.main:app --host 127.0.0.1 --port 8654

# frontend on 5200, pointed at it
cd app/frontend && VITE_ENTRA_TENANT_ID="" VITE_ENTRA_CLIENT_ID="" \
  VITE_API_BASE="http://127.0.0.1:8654" npx vite --host 127.0.0.1 --port 5200
```

### The sign-in scene

The Microsoft credential screen is **not filmed**. The scene shows our own
screens and captions what happens at Microsoft. Filming a mocked-up Microsoft
page would teach the team to trust a login screen we drew ourselves, which is
exactly the habit phishing relies on. If a real sign-in must be shown, record
those few seconds by hand and keep them clearly separate.

### Checking a run without watching it

Each run writes a frame per scene to `/tmp/onefcst-demo-record/shots/`. The
failure that actually happens is a scene filming a half-loaded page, and the
frames show it in seconds. `nav()` waits for network idle for this reason —
the first take filmed the forecast screen with every tile still a dash.

### Length

The brief is three minutes. `SPEED` at the top scales every hold at once, and
the script prints its real elapsed time and warns past 190s. Adjust `SPEED`
rather than deleting a scene — the point is to cover the whole workflow.
