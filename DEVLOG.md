# DEVLOG — chatgpt-export

How this tool got built, focused on the final design and the things worth knowing if you ever need to maintain or extend it. Brief mentions of approaches that didn't pan out, in case you're tempted to try the same.

## Goal

Extract a ChatGPT user's full chat history to Markdown and raw JSON before an enterprise tenant gets deleted, with about two weeks of runway. Two constraints made the obvious paths unavailable:

- **Settings → Data Controls → Export data** is admin-disabled at the tenant level.
- The tool needs to work for any employee in the tenant, on Linux, macOS, or Windows — so cookie extraction has to be possible from a normal browser's DevTools, not just by developers with `browser-harness` or similar.

## Final shape

One file, `export.py`, no third-party dependencies, Python 3.10+. Resumable: each conversation's raw JSON is cached on disk, so a 401 mid-run isn't fatal — re-run and it picks up where it left off. Output for each conversation:

- `data/raw/<full-uuid>.json` — unmodified API response, preserved as the forensic safety net.
- `data/markdown/<YYYY-MM-DD>_<slug>_<id8>.md` — rendered active-path Markdown with YAML frontmatter (title, id, model, created/updated, message counts, branch flag).
- `data/files/<id8>/<file_id>_<name>` — uploaded attachments and generated images, downloaded inline.

End-user setup is just **paste a `Cookie:` header from DevTools into `.secrets/cookies.txt`** and run. See `README.md`.

## How the export works

1. **Cookie → access token.** The script reads the chatgpt.com `Cookie` header from `.secrets/cookies.txt` (preferred) or `.secrets/cookies.json` (a CDP-style dump, used by `browser-harness`-equipped developers). It then GETs `chatgpt.com/api/auth/session` with that cookie, which returns an `accessToken` (an RS256 JWT, ~2 KB). The token expires after a while; the client re-mints on demand and on 401.
2. **Walk the conversation list.** GET `/backend-api/conversations?offset=N&limit=100&order=updated` until a short page indicates end-of-list. Each list item carries `id`, `title`, `update_time`, `workspace_id`.
3. **Fetch each tree.** GET `/backend-api/conversation/{id}` returns the full message tree with `mapping` (`{node_id: {id, parent, children, message}}`) and `current_node` (the leaf of the active branch).
4. **Render Markdown.** Walk from `current_node` back to root via each node's `parent`, reverse, render. Filter out hidden / system / memory-injection messages. Strip ChatGPT's Private Use Area citation markup (see below). Download referenced files.
5. **Cache aggressively.** Raw JSON is cached on disk before rendering. Re-running the export skips conversations whose raw JSON is already on disk; `--rerender` regenerates Markdown from cache without any API calls.

## Why this approach

Three options were considered up front. We narrowed down quickly:

1. **Official export** — ruled out, admin-disabled.
2. **Internal API via the logged-in session** — chosen. A single probe (one `GET /backend-api/conversations?limit=1` from the browser's page context) confirmed the API is open for a `standard-user` account on this enterprise tenant. The data is structured, lossless, and gives us the full message tree (including alternate branches) instead of just the rendered DOM.
3. **Page-state extraction** (read the JSON ChatGPT hydrates into the React tree when a conversation is open) — kept in mind as a known-good fallback if a future tenant locks down `/backend-api/...`. Same data shape; would mean opening each chat in a real browser instead of using the JSON API.

We initially ran the API requests via a `fetch()` injected into the page through `browser-harness`. That worked for small probes but a single batched probe queued enough requests to wedge the page's renderer (the harness `Runtime.evaluate` call timed out, but the queued JavaScript kept running in the background and blocked all subsequent calls). Switching to **cookies-out-of-the-browser + plain Python `urllib`** removed the page from the loop entirely after one extraction step. This also dropped the dependency on `browser-harness` for end users and is what makes the tool installable on any platform.

The cookies were rescued from the wedged page using `cdp("Network.getCookies", ...)` — that runs in Chrome's browser process, not the renderer, so it kept working while the page was hung. That's how `cookies.json` got introduced as a developer-friendly second input format alongside the DevTools-pasted `cookies.txt`.

## Things worth knowing for the next maintainer

### The `total` field is a pagination hint, not a count

`/backend-api/conversations?...&limit=N` returns `total = min(true_total, N + 1)`. It tells you "is there at least one more page" by returning `N+1` — it is **not** a count of all conversations. Confirmed across `limit=1 → total=2`, `limit=10 → total=11`, `limit=100 → total=101`. **Pagination logic must check `len(items) < limit`, not `total`.** If you only inspect a small page you'll get a misleadingly small `total` and conclude the account is nearly empty. It is not. The full walk (page through `limit=100` until short page) is the only honest count.

### ChatGPT injects Private Use Area citation markup into message text

The API returns inline citation markers as Unicode Private Use Area characters that render as missing-glyph boxes in any normal editor:

| codepoint | role |
|---|---|
| `U+E200 … U+E201` | wraps a citation reference (the inner text is the citation id, e.g. `cite|turn0search0`) |
| `U+E203 … U+E204` | wraps a sentence quoted from search results |
| `U+E202`, `U+E205` | separators inside the above |
| `U+E206` | end-of-cluster marker |

ChatGPT's web UI substitutes these at render time using `metadata.citations` and `metadata.content_references` to look up real URLs. We tried to do the same and couldn't — every entry on the conversations sampled had `"invalid_reason": "Unsupported, please upgrade"` or `"refs": ["hidden"]`. The structured citation data has rotted out from under older conversations.

The `clean_pua()` function in `export.py` strips the markup and keeps the visible text. The raw JSON in `data/raw/` preserves everything for any future better citation reconstructor.

### Conversation tree shape

Most likely to surprise:

- A conversation is a **tree of messages**, not a list. Edits and regenerations create sibling nodes. The active path the UI shows you is the chain from `current_node` back to root, traversed via each node's `parent`.
- Many `mapping` nodes have `message: null` — orphan placeholders from edits/branches. Skip them.
- `metadata.is_visually_hidden_from_conversation` is the official "don't render" flag. It's the primary visibility filter (covers memory injections, system prompts, contextual-answers boilerplate).
- `content.content_type` varies: `text` (common), `model_editable_context` (memory, hide), `code` (code-interpreter cell), `multimodal_text` (parts contain image asset pointers), `tether_browsing_display` and `tether_quote` (browse-tool surfaces), `execution_output` (code-interpreter output).
- A user-uploaded file appears *both* as `metadata.attachments[]` (with name/mime/size) and as a `dict` part with `content_type: image_asset_pointer` (etc.) inside `content.parts`. We render once, using `attachments[]` for the metadata.
- Asset pointers come in two URL schemes: `sediment://file_XXX` (newer) and `file-service://file_XXX` (older). Strip the scheme to get the file id, then `GET /backend-api/files/{file_id}/download` returns `{"download_url": <signed URL>}` that needs no auth to fetch.

### `update_time` shifts during read

`update_time` on conversations isn't stable across reads within a session — possibly because GET on `/backend-api/conversation/{id}` triggers a server-side touch, or because some background job updates conversations near read-time. **Doesn't affect a full export** — you fetch all of them either way — but it means `--limit 5` may not give you "the 5 chats at the top of your sidebar right now."

## Bugs found during the live run

Both of these are the kind of thing you only see when running for real over the full account. Worth surfacing here so the next maintainer knows the design choices were earned.

### Rate-limit retries were originally too tight

First real run, with the initial defaults (4 retries, 1 → 2 → 4 → 8 s exponential backoff, 0.3 s pacing between requests), hit `HTTP 429` on `/backend-api/conversation/{id}` around the 230th conversation and burned through all retries on **4 conversations** before the rate-limit window cleared. Those conversations were not cached (no raw JSON written) so they were eligible for a retry on the next run, but ideally the script should ride out a 429 burst on first attempt.

Fix: bumped retries to 6, started backoff at 10 s and capped at 60 s (worst-case wait ≈ 4 min), and slowed inter-request pacing from 0.3 s to 1.0 s. With these settings the run no longer loses conversations to rate limiting — but the observed sustained rate is **much** slower than the initial estimate. ChatGPT's per-account rate limit on `/backend-api/conversation/{id}` is aggressive enough that the typical pattern under load is "fetch one, sleep 10 s on a 429, fetch one, sleep 20 s, ..." — roughly **1 fetch per minute** once the rate-limit floor kicks in. For a ~1,300-conversation account that means the full export is an **overnight job** (~15–24 h), not the ~30–40 min we'd initially hoped for. The run is resumable, so killing and restarting is safe; the cached raw JSON is skipped on the next run. Commit `2118108`.

If a future ChatGPT version sends `Retry-After` on its 429s, the existing handler honours that header, so it'll automatically take precedence over our manual backoff. Worth checking response headers next time someone re-runs this — `Retry-After`-driven pacing would in principle be friendlier than blind backoff, though we never observed it being sent.

### Restarts re-rendered every cached conversation

Original loop always called `_write_md()` per iteration, even when the raw JSON was cached and the rendered Markdown already on disk. On a restart of a partially-completed run, that meant re-serialising every cached conversation's Markdown — minutes of disk I/O before the script reached the first conversation that actually needed fetching. From outside, the markdown directory looked busy while no real progress was being made on the API side; the user pointed this out while watching the run.

Fix: before doing anything, glob for `data/markdown/*_<id8>.md`. If both the raw JSON and a Markdown file matching the conversation's id8 exist, skip with no I/O. `--rerender` mode is unaffected (it explicitly forces re-rendering from cache). Commit `59df5c2`.

A side effect of the glob check is that title changes between runs leave a stale Markdown file with the old name. Acceptable — `--rerender` regenerates everything from cache, and the raw JSON is the canonical record.

## Timeline — idea to running tool

Wall-clock from problem statement to a stable running export, all on 2026-04-29:

| time | event |
|---|---|
| 20:00 | Project kickoff: "we need to export ChatGPT chats before the tenant gets deleted" |
| 20:03 | DEVLOG started |
| 20:11 | Cookies extracted via `cdp("Network.getCookies", ...)` after the page-JS approach wedged the browser tab |
| 20:14 | Plain-Python flow validated end-to-end: cookie → `/api/auth/session` → bearer → `/backend-api/conversations` returns 200 |
| 20:22 | First 5-conversation sample rendered to Markdown |
| 20:25 | PUA citation-markup gotcha caught (the "strange symbols" the user noticed) and `clean_pua()` fixed it |
| 20:32 | Project pivoted from "personal export" to "self-serve tool other employees can use" — required a non-`browser-harness` cookie path |
| 20:36 | Refactored for general use: `cookies.txt` path, `--check` flag, Windows-safe filenames |
| 20:42 | Initial release pushed to `GagnaveitaReykjavikur/chatgpt-export` (commit `e936bf0`) — README, DEVLOG, LICENSE, the lot |
| 20:48 | First real-run failure: 4 conversations lost to `HTTP 429` with the original tight backoff |
| 20:53 | Calmer rate-limit defaults committed and pushed; the export that's still running started here |
| 20:59 | Skip-rerender bug caught (user noticed it from the markdown directory churn during restart) and fixed |

So roughly **45 minutes from idea to tool published on GitHub**, and **~55 minutes to a stable running export**. The remaining wall-clock (currently a projected ~17 h to finish 1,326 conversations under ChatGPT's rate-limiter) is network-bound: the code was done at 20:53. Most of the design lessons in this DEVLOG were discovered between 20:00 and 20:53 — the rest of the "wall clock" is just data moving.

## Run results — finish stats and post-export polish

The first full export of the user's account completed cleanly. Final tally from the script:

```
fetched 1107, skipped 219, rendered 1326, failed 0
```

| | |
|---|---|
| Wall-clock | ~18 h 27 min (yesterday 20:53 → today 15:20) |
| Conversations exported | **1,326 / 1,326** |
| HTTP 429 retries ridden out | **1,400+** during the run, **0** lost |
| Raw JSON | 127 MB across 1,326 files |
| Markdown | 34 MB across 1,326 files |
| Attachments downloaded | 1.1 MB across 263 conversation directories |

The realized wall-clock matched the ~15–24 h estimate published in the README the previous day, so nothing surprising on the rate-limit front.

### Sanity sample across the full corpus

A pass over all 1,326 rendered Markdown files (rather than the 5 we eyeballed earlier) surfaced a few things worth recording:

- **`workspace:` empty in every frontmatter.** The list endpoint returns `workspace_id` per item, but the per-conversation tree endpoint does not — so `j.get("workspace_id")` was always `None` at render time. Fixed by stashing the list-item's `workspace_id` under `_workspace_id` on the cached raw JSON at fetch time, and a one-shot backfill walked the list once to patch all 1,326 already-cached files. Rerendering after backfill: empty workspace count `0 / 1326`. Commit pending.
- **1,500 attachments returned `download failed`.** ChatGPT auto-expires generated images and uploaded files after a retention window; the file_ids in those messages are no longer fetchable. The raw JSON preserves the file_ids and any associated metadata, so a future recovery pass could try again, but most are likely permanently gone. Not a fixable bug.
- **134 PUA characters across 7 files (`U+F0D6` etc).** Different range from ChatGPT's citation markup (`U+E200–E20F`) — these are Wingdings / Word symbol-font artifacts in *user-pasted* content (e.g., a financial report bulleted with ``). Not a render bug; legit user input. Left alone.
- **5 messages with `content_type: "system_error"`** (ValueError, NoDocumentsFound, etc) hit the unknown-content_type fallback and rendered as a fenced JSON dump — content preserved, just not pretty. Could add a dedicated renderer if it shows up more often.
- **33 conversations have alternate branches** (`has_alternate_branches: true`). Active path rendered as designed; the alternates are still in the raw JSON.

### `--rerender` was making thousands of network calls

While re-running the rerender after the workspace fix, noticed `--rerender` was hanging — because the renderer's attachment-download path was attempting *every* previously-failed `file_id` afresh. With 1,500 known-failed attachments and ChatGPT's rate limits, a "no API" rerender was actually 50+ minutes of mostly-failing API calls.

Fix: make `--rerender` pass `client=None` into the renderer, which short-circuits `_download_attachment` to "use what's already on disk, otherwise emit a `download failed` placeholder." Also moved the rerender branch in `run()` to *before* the `mint_token` call, so an expired cookie no longer blocks re-rendering an already-fetched archive (which had also surfaced during this work — the session token had rotated during the 18-hour run). Rerender now runs in ~2.4 seconds for 1,326 conversations. Commit pending.

## Conventions for this DEVLOG

- Newest day on top. Sub-sections under each day's heading.
- Record probe results with the exact endpoint, status, and a one-line takeaway. Future-you shouldn't have to re-run the probe to remember what it returned.
- When a hypothesis is killed, write what killed it, briefly. "Tried X, got Y, abandoned" is more useful than silence.
- Keep tenant-specific details (workspace UUIDs, account IDs, conversation titles) out of this file. The repo is private to the org, but every employee using the tool reads it — don't leak signal about who has been doing what.
