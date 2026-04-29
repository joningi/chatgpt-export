# chatgpt-export

Self-serve markdown export of your ChatGPT chat history — for accounts where the official **Settings → Data Controls → Export** has been disabled by an administrator (e.g. an enterprise tenant being shut down).

Walks the same `/backend-api` endpoints the ChatGPT website itself uses, pulling each conversation as raw JSON and rendering a clean Markdown file alongside it. Attachments and images are downloaded too.

- **Stdlib only.** No `pip install` step. Python 3.10+.
- **Cross-platform.** Linux, macOS, Windows.
- **Resumable.** Each conversation's raw JSON is cached on disk; re-runs skip what's already pulled.
- **Read-only.** No mutations on your account; only `GET` requests.

## Output

For each chat in your account you get three things:

```
data/
├── raw/<full-conversation-uuid>.json          # unmodified API response, your forensic safety net
├── markdown/<YYYY-MM-DD>_<slug>_<id8>.md      # rendered markdown (see below)
└── files/<id8>/<file_id>_<original_name>      # uploaded attachments + generated images
```

The Markdown file uses YAML frontmatter and standard Markdown body — it opens cleanly in Obsidian, VS Code, GitHub, and any static-site generator.

```markdown
---
title: "IPv4 Lease Pricing"
id: 69f1ecb2-03c4-8333-a874-57f9d723a30a
created: 2026-04-29T11:34:14+00:00
updated: 2026-04-29T11:59:23+00:00
model: gpt-5-3
messages_visible: 9
messages_in_path: 16
nodes_in_tree: 17
has_alternate_branches: false
---

# IPv4 Lease Pricing

### User
_2026-04-29T11:34:13+00:00_

ipv4 lease price

### Assistant
_`gpt-5-3` · 2026-04-29T11:34:13+00:00_

…
```

System / memory injections and explicitly-hidden messages are filtered out (the gap between `messages_visible` and `messages_in_path` shows how many). Only the **active branch** of each conversation is rendered (what the ChatGPT UI shows you today). If a chat had edits or regenerations, the alternate branches stay preserved in the raw JSON for later recovery; the Markdown frontmatter sets `has_alternate_branches: true` to flag those.

## Quick start

```bash
git clone https://github.com/GagnaveitaReykjavikur/chatgpt-export
cd chatgpt-export

# 1. Get your cookies (one-time, see "Extract cookies" below)
#    → save as .secrets/cookies.txt

# 2. Verify your credentials work
python3 export.py --check

# 3. Trial run on the 5 most recent conversations
python3 export.py --limit 5

# 4. Full export
python3 export.py
```

A full run is serial with a 0.3 s pause between requests; expect roughly **1 second per conversation**. 1,000 chats ≈ 15 minutes.

## Prerequisites

### Linux

Most distros already have Python 3.10+ installed. Check:

```bash
python3 --version
```

If missing, install via your package manager (e.g. `sudo pacman -S python` on Arch / Omarchy, `sudo apt install python3` on Debian/Ubuntu).

### macOS

macOS 12.3+ ships with `python3`. Check:

```bash
python3 --version
```

If you hit `urllib.error.URLError: [SSL: CERTIFICATE_VERIFY_FAILED]` the system Python's certificate bundle isn't installed. Fix it once with:

```bash
/Applications/Python\ 3.*/Install\ Certificates.command
```

(or `brew install python` and use that instead).

### Windows

Install Python from <https://www.python.org/downloads/windows/> (3.10 or newer). During setup, tick **"Add python.exe to PATH"**.

Open **PowerShell** (not the legacy `cmd`) and substitute `python` for `python3` everywhere in this README:

```powershell
python --version
python export.py --check
```

PowerShell handles UTF-8 output correctly; the legacy `cmd` console may garble Icelandic / non-ASCII conversation titles in console output (the saved files are still UTF-8 either way).

## Extract cookies

The script needs the cookies your browser uses when you're logged in to ChatGPT. There are two supported formats; for a one-off export, **the text method is simpler**.

### Option A — paste a Cookie header (recommended)

1. In your browser, log in to <https://chatgpt.com> with the account you want to export.
2. Open **DevTools**:
   - Chrome / Edge: `Ctrl+Shift+I` (Windows/Linux) or `Cmd+Opt+I` (macOS)
   - Firefox: `Ctrl+Shift+E` (Windows/Linux) or `Cmd+Opt+E` (macOS)
3. Switch to the **Network** tab. Reload the page if no requests appear.
4. Find any request whose URL starts with `https://chatgpt.com/backend-api/` (clicking around the sidebar in ChatGPT will trigger several). Click on it.
5. In the right-hand panel, switch to **Headers** and scroll down to the **Request Headers** section.
6. Locate the `Cookie:` line. **Right-click the value** (the long string after `Cookie:`) and choose **Copy value**.
7. Create the file `.secrets/cookies.txt` in this repo and paste the copied string into it. The file should contain exactly one line that looks like:

   ```
   __Secure-next-auth.session-token=eyJ…; oai-did=…; __cf_bm=…; (etc)
   ```

8. Verify it works:

   ```bash
   python3 export.py --check
   ```

   You should see your own email and `list status: HTTP 200`.

That's it. The cookies stay on your local machine — they are **not** committed to git (`.secrets/` is in `.gitignore`).

### Option B — JSON dump (power users)

If you have [browser-harness](https://github.com/browser-use/browser-harness) attached to your Chrome, you can dump cookies via CDP — works even when the page itself is unresponsive:

```bash
browser-harness -c '
import json, os
res = cdp("Network.getCookies", urls=["https://chatgpt.com/"])
os.makedirs(".secrets", exist_ok=True)
open(".secrets/cookies.json", "w").write(json.dumps(res["cookies"], indent=2))
print("saved", len(res["cookies"]), "cookies")
'
```

`export.py` accepts either `cookies.txt` (preferred) or `cookies.json` automatically.

## Usage

```
python3 export.py [--check] [--limit N] [--rerender] [--refresh-token]
```

| Flag | Purpose |
|---|---|
| (no args) | Full export. Walks every conversation, fetches raw JSON, renders Markdown, downloads attachments. Resumable — skips conversations whose raw JSON is already on disk. |
| `--check` | Verify your cookies work. Mints an access token and pings the conversations endpoint. **Run this first to confirm setup before a real export.** |
| `--limit N` | Only process the first N conversations from the list. Use for a trial run to confirm output format looks right before the full export. |
| `--rerender` | Re-render Markdown from already-cached raw JSON without making any API calls. Useful if you've updated the renderer and want fresh Markdown without re-fetching. |
| `--refresh-token` | Just mint a fresh access token from your session cookie. Useful for diagnosing whether `cookies.txt` is still valid. |

### Suggested run order for first time

```bash
# 1. Confirm cookies are good
python3 export.py --check

# 2. Render the most-recent 5 conversations and eyeball them
python3 export.py --limit 5
ls data/markdown/

# 3. If anything looks wrong, fix it / re-render
python3 export.py --rerender

# 4. Full export
python3 export.py
```

## Troubleshooting

### `no credentials found`

You haven't created `.secrets/cookies.txt` yet. See [Extract cookies](#extract-cookies).

### `no accessToken in /api/auth/session — cookies likely expired`

Cookies expire (the session token typically lasts a few weeks, but Cloudflare tokens rotate hourly). Re-extract cookies from DevTools (Option A) and try again. `--refresh-token` is a useful diagnostic.

### `HTTP 401` mid-run

Same as above — session expired during a long run. Refresh `cookies.txt` and re-run; the export is resumable, so it'll pick up where it left off (already-fetched conversations are cached in `data/raw/`).

### `HTTP 429`

Rate limit. The script automatically retries with exponential backoff and honours `Retry-After` headers, but if you've been hammering the API from another tool too you may need to wait a few minutes.

### `urllib.error.URLError: [SSL: CERTIFICATE_VERIFY_FAILED]` on macOS

Run `/Applications/Python\ 3.*/Install\ Certificates.command` (one-time fix), then retry.

### Strange box characters / missing glyphs in the rendered Markdown

ChatGPT's API includes Unicode Private Use Area characters as inline citation markers (`U+E200`–`U+E206`). The renderer strips these. If you see them, you're looking at the **raw JSON**, not the Markdown — those preserve everything for forensic recovery. See `DEVLOG.md` for details.

### Windows: garbled non-ASCII characters in console output

Use **PowerShell**, not the legacy `cmd` console. The saved files are UTF-8 either way.

## Security notes

- The cookies in `.secrets/cookies.txt` are **equivalent to your password** for ChatGPT — anyone with that file can sign in as you until the session expires. Treat the file accordingly. `.secrets/` is in `.gitignore`; do not commit it.
- The script is read-only against your account — it only issues `GET`s — but the access token it mints can theoretically do anything the website can. Don't share it.
- If you suspect a leak, sign out of all ChatGPT sessions in **Settings → Security → Sessions** to invalidate the cookie.

## How it works (short version)

The ChatGPT website talks to its own backend at `chatgpt.com/backend-api/...` using a **bearer token** that the page mints from your session cookie via `chatgpt.com/api/auth/session`. This script does the same thing, from Python: it copies your cookie, calls `/api/auth/session` to get the bearer, then walks `/backend-api/conversations` (paginated) and `/backend-api/conversation/{id}` (per-tree) to assemble the archive. Attachments are resolved through `/backend-api/files/{file_id}/download`, which returns a signed S3-style URL.

For a fuller chronological walkthrough including the dead ends and lessons learned while building this, see [`DEVLOG.md`](DEVLOG.md).

## License

MIT — see [`LICENSE`](LICENSE).
