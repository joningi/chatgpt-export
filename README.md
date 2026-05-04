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

A full run is serial with a 1 s pause between requests. **Plan for it to take a long time.** ChatGPT applies aggressive per-account rate limits on the conversation-fetch endpoint, and the script rides them out with backoff (10 s → 60 s, up to 6 attempts per request). The sustained rate observed in practice is roughly **1 conversation per minute** once the rate-limit floor kicks in:

- ~100 chats: 30 min – 1 hour
- ~500 chats: 6 – 10 hours
- ~1,000+ chats: **plan for an overnight run** (15 – 24 hours)

The script is resumable — kill it, sleep, restart, it picks up where it left off (already-fetched raw JSON is cached on disk and skipped).

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

> **Non-technical Windows user?** See [Step-by-step Windows walk-through](#step-by-step-windows-walk-through) below for a literal click-by-click guide.

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

## Step-by-step Windows walk-through

For non-technical users on Windows. Allow ~10 minutes of setup, plus the actual export time (see *Quick start* above for size-based estimates — large accounts are an overnight job).

You'll need a GitHub account that's a member of the **GagnaveitaReykjavikur** org so you can access this private repo. If you can open <https://github.com/GagnaveitaReykjavikur/chatgpt-export> in your browser, you're in.

### 1. Install Python

If you don't already have Python 3.10 or newer installed:

1. Go to <https://www.python.org/downloads/windows/>.
2. Click the highlighted **"Download Python 3.x.x"** button at the top.
3. Run the downloaded installer.
4. **IMPORTANT:** on the first installer screen, tick **"Add python.exe to PATH"** at the bottom *before* clicking *Install Now*.
5. Wait for the install to finish, then close the installer.

Verify: open PowerShell (Start menu → type "PowerShell" → click *Windows PowerShell*). At the prompt, run:

```powershell
python --version
```

You should see `Python 3.10.x` or higher. If you see `'python' is not recognized`, the PATH checkbox above wasn't ticked — re-run the installer and check it.

### 2. Download the tool

1. In your browser, go to <https://github.com/GagnaveitaReykjavikur/chatgpt-export> and sign in to GitHub with your work account if prompted.
2. Click the green **"<> Code"** button.
3. Click **"Download ZIP"** at the bottom of the dropdown.
4. Open your **Downloads** folder. Right-click `chatgpt-export-main.zip` → **Extract All...** → click **Extract**.

You should now have `Downloads\chatgpt-export-main\chatgpt-export-main\`. Open the inner folder so you can see files like `README.md` and `export.py`.

### 3. Open PowerShell in the project folder

In the File Explorer window showing the inner `chatgpt-export-main` folder:

1. Click on the address bar at the top of the window (where the path is displayed).
2. Delete what's there, type `powershell`, and press **Enter**.

A blue PowerShell window opens, already in the right folder. (If `powershell` doesn't work, try `pwsh` instead — that's PowerShell 7.)

### 4. Get your ChatGPT cookies

The script needs the cookies your browser uses when you're signed in to ChatGPT. **They are equivalent to your password** — anyone with that file can sign in as you until the session expires. Keep it private. (The `.secrets\` folder is gitignored, so it won't accidentally end up committed back to the repo.)

#### 4a. Open ChatGPT and DevTools

1. In Chrome or Edge, go to <https://chatgpt.com>. **Make sure you're signed in with the account you want to export.**
2. Press `Ctrl + Shift + I` to open DevTools. (Alternatively: right-click anywhere on the page → *Inspect*.)

#### 4b. Find a request to copy headers from

1. In DevTools, click the **Network** tab at the top.
2. The list will probably look empty at first. To populate it, click around the ChatGPT sidebar — open one of your existing chats, or press `F5` to refresh the page. Many requests will appear.
3. In the small **Filter** box near the top of the Network tab, type `backend-api`. The list will narrow to just the requests carrying your auth.
4. Click on **any one of the listed requests** — they all carry the same cookies.

#### 4c. Copy the Cookie value

1. With the request selected, look at the right-hand panel of DevTools. Click the **Headers** tab in that panel.
2. Scroll down to the **Request Headers** section.
3. Find the line that starts with `Cookie:`. The value is a single very long string — typically several thousand characters — that begins something like `__Secure-next-auth.session-token=...; oai-did=...; __cf_bm=...;` and so on.
4. **Right-click on the value** (not on the word "Cookie:") and choose **Copy value**.

If you don't see "Copy value" in the right-click menu, your DevTools version uses slightly different wording — try **Copy** or **Copy → Cookie value**. As a last resort: click into the value, press `Ctrl+A` to select all of it, then `Ctrl+C` to copy.

#### 4d. Save it to disk

Switch back to your PowerShell window (still in the project folder) and run:

```powershell
New-Item -ItemType Directory -Path .secrets -Force | Out-Null
Get-Clipboard -Raw | Set-Content -Path .secrets\cookies.txt -NoNewline
```

That creates the `.secrets` folder and writes whatever's on your clipboard into `cookies.txt`.

#### 4e. Sanity check the file

```powershell
(Get-Item .secrets\cookies.txt).Length
```

You should see a number in the **thousands** (typically 5,000–12,000). If it's much smaller than that, the wrong thing got copied — go back to step 4c and try again. If it's `0`, the clipboard was empty when you ran the save command — copy the value again and re-run the save command in 4d.

Cookies eventually expire (typically a few weeks for the session token, but Cloudflare tokens rotate hourly). If a long-running export starts hitting `HTTP 401`, just redo step 4 — the export resumes from cache where it left off.

### 5. Verify your cookies work

```powershell
python export.py --check
```

You should see your own email address and `list status: HTTP 200`. If you get `no accessToken in /api/auth/session — cookies likely expired`, your cookies are stale or you copied the wrong line — go back to step 4.

### 6. Trial run on the first 5 conversations

```powershell
python export.py --limit 5
```

This fetches and renders the 5 most-recent conversations. Open `data\markdown\` in File Explorer and double-click one of the `.md` files — it should open in Notepad (or your preferred editor) and look like a clean transcript of that chat.

### 7. Full export

```powershell
python export.py
```

This runs through every conversation. **For accounts with hundreds of conversations this is an overnight job** — see the time estimates at the top of this README. The script prints progress per conversation and is resumable: if you need to stop it (`Ctrl+C`), just re-run the same command — it will skip what's already on disk and pick up where it left off.

You can leave the PowerShell window open and minimize it. Don't close it; that would stop the export.

### 8. Find your output

When the export finishes, your data is in three folders inside the project:

- `data\markdown\` — one `.md` file per conversation. Open in Notepad, VS Code, Obsidian, or any Markdown viewer.
- `data\raw\` — the unmodified API responses, useful as a backup if anything in the Markdown looks off.
- `data\files\` — uploaded attachments and any images generated in your chats.

Copy the entire `data\` folder to OneDrive, an external drive, or wherever your personal long-term archive lives. That's your saved history.

## Troubleshooting

### `no credentials found`

You haven't created `.secrets/cookies.txt` yet. See [Extract cookies](#extract-cookies).

### `no accessToken in /api/auth/session — cookies likely expired`

Cookies expire (the session token typically lasts a few weeks, but Cloudflare tokens rotate hourly). Re-extract cookies from DevTools (Option A) and try again. `--refresh-token` is a useful diagnostic.

### `HTTP 401` mid-run

Same as above — session expired during a long run. Refresh `cookies.txt` and re-run; the export is resumable, so it'll pick up where it left off (already-fetched conversations are cached in `data/raw/`).

### Many `HTTP 429 — sleeping ...s` messages / very slow run

**Expected.** ChatGPT applies aggressive per-account rate limits on the `/backend-api/conversation/{id}` endpoint, and they kick in within the first few hundred fetches on a full export. The script's backoff (10 s → 60 s, up to 6 attempts per request, honouring `Retry-After`) rides them out without losing data. Don't kill the process when you see these messages — it's working as intended.

Sustained rate under throttle: roughly **1 conversation per minute**. Large accounts are an overnight job. The run is resumable, so you can stop and restart at any time.

### `urllib.error.URLError: [SSL: CERTIFICATE_VERIFY_FAILED]` on macOS

Run `/Applications/Python\ 3.*/Install\ Certificates.command` (one-time fix), then retry.

### Strange box characters / missing glyphs in the rendered Markdown

ChatGPT's API includes Unicode Private Use Area characters as inline citation markers (`U+E200`–`U+E206`). The renderer strips these. If you see them, you're looking at the **raw JSON**, not the Markdown — those preserve everything for forensic recovery. See `DEVLOG.md` for details.

### Windows: garbled non-ASCII characters in console output

Use **PowerShell**, not the legacy `cmd` console. The saved files are UTF-8 either way.

### Windows: `UnicodeEncodeError: 'charmap' codec can't encode character`

Older versions wrote files using the system's default encoding (cp1252 on Western Windows installs), which can't represent `→`, emoji, or the `U+E200…` citation markers that occasionally appear in chats. Fixed in commit `6694e89` — pull or re-download the latest version. Workaround on a stale checkout: `python -X utf8 export.py`.

## Security notes

- The cookies in `.secrets/cookies.txt` are **equivalent to your password** for ChatGPT — anyone with that file can sign in as you until the session expires. Treat the file accordingly. `.secrets/` is in `.gitignore`; do not commit it.
- The script is read-only against your account — it only issues `GET`s — but the access token it mints can theoretically do anything the website can. Don't share it.
- If you suspect a leak, sign out of all ChatGPT sessions in **Settings → Security → Sessions** to invalidate the cookie.

## How it works (short version)

The ChatGPT website talks to its own backend at `chatgpt.com/backend-api/...` using a **bearer token** that the page mints from your session cookie via `chatgpt.com/api/auth/session`. This script does the same thing, from Python: it copies your cookie, calls `/api/auth/session` to get the bearer, then walks `/backend-api/conversations` (paginated) and `/backend-api/conversation/{id}` (per-tree) to assemble the archive. Attachments are resolved through `/backend-api/files/{file_id}/download`, which returns a signed S3-style URL.

For a fuller chronological walkthrough including the dead ends and lessons learned while building this, see [`DEVLOG.md`](DEVLOG.md).

## License

MIT — see [`LICENSE`](LICENSE).
