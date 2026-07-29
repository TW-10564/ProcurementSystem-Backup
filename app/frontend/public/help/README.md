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
