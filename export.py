"""ChatGPT conversation exporter — active-path markdown + raw JSON + attachments.

Reads the chatgpt.com Cookie header from `.secrets/cookies.txt` (preferred) or
`.secrets/cookies.json` (CDP-style dump). Mints an accessToken from
`/api/auth/session`. Walks every conversation, saves the raw tree (safety net)
and a rendered Markdown file with attachments downloaded alongside.

Resumable: skips conversations whose raw JSON is already cached. Re-rendering
Markdown from cached raw JSON is free (`--rerender`).

Usage:
    python3 export.py --check          # verify cookies + auth surface, then exit
    python3 export.py --limit 5        # trial run on the first 5 conversations
    python3 export.py                  # full export (serial)
    python3 export.py --rerender       # re-render markdown from cached raw JSON
    python3 export.py --refresh-token  # mint a fresh access token only

See README.md for cookie-extraction instructions and full setup. If the script
ever returns HTTP 401, your session cookie expired — refresh `cookies.txt` from
DevTools and re-run; the export is resumable.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
SECRETS = ROOT / ".secrets"
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
MD_DIR = DATA / "markdown"
FILES_DIR = DATA / "files"

BASE = "https://chatgpt.com"
UA = "Mozilla/5.0 (chatgpt-export)"


# ---------- credentials ----------

def cookie_header(cookies: list[dict], host: str = "chatgpt.com") -> str:
    parts = []
    for c in cookies:
        d = (c.get("domain") or "").lstrip(".")
        if d and not (host == d or host.endswith("." + d)):
            continue
        parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def load_creds() -> str:
    """Load the chatgpt.com Cookie header. Two accepted sources, in priority order:

    1. .secrets/cookies.txt — a single line: the raw `Cookie:` header value
       copied straight from DevTools (Network → /backend-api/* → Headers →
       Cookie → right-click value → Copy). The simplest path for end users.
    2. .secrets/cookies.json — CDP-style array of {name, value, domain, ...}
       dicts produced by `cdp("Network.getCookies", urls=...)`. Used when the
       cookies were extracted via browser-harness.
    """
    txt = SECRETS / "cookies.txt"
    js = SECRETS / "cookies.json"
    if txt.exists():
        raw = txt.read_text(encoding="utf-8").strip()
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1].strip()
        return raw
    if js.exists():
        return cookie_header(json.loads(js.read_text(encoding="utf-8")))
    raise RuntimeError(
        "no credentials found. Create one of:\n"
        f"  {txt}   (paste the Cookie header from DevTools — see README)\n"
        f"  {js}    (CDP-style cookie dump from browser-harness)\n"
    )


def mint_token(cookie: str) -> tuple[str, dict]:
    h = {"User-Agent": UA, "Accept": "application/json", "Cookie": cookie}
    req = urllib.request.Request(f"{BASE}/api/auth/session", headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        sess = json.loads(r.read().decode("utf-8"))
    token = sess.get("accessToken")
    if not token:
        raise RuntimeError(f"no accessToken in /api/auth/session — cookies likely expired. session keys: {list(sess.keys())}")
    return token, sess.get("user") or {}


# ---------- HTTP ----------

class Client:
    def __init__(self, cookie: str, token: str):
        self.cookie = cookie
        self.token = token

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "User-Agent": UA,
            "Accept": "application/json",
            "Cookie": self.cookie,
            "Authorization": f"Bearer {self.token}",
        }
        if extra:
            h.update(extra)
        return h

    def get(self, path: str, *, raw: bool = False, retries: int = 6) -> tuple[int, bytes | dict]:
        url = path if path.startswith("http") else BASE + path
        # Start backoff at 10 s, double, cap at 60 s. With retries=6 the worst-
        # case wait is 10+20+40+60+60+60 = 250 s (~4 min) before giving up,
        # which is enough to ride out most ChatGPT 429 windows.
        delay = 10.0
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    return r.status, body if raw else json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as e:
                code = e.code
                if code == 401:
                    # try refresh once
                    token, _ = mint_token(self.cookie)
                    self.token = token
                    if attempt == 0:
                        continue
                    return code, e.read()
                if code in (429, 500, 502, 503, 504):
                    ra = e.headers.get("Retry-After")
                    sleep_for = float(ra) if ra and ra.replace(".", "").isdigit() else delay
                    print(f"    HTTP {code} on {path[:80]} — sleeping {sleep_for:.1f}s (attempt {attempt+1}/{retries})", flush=True)
                    time.sleep(sleep_for)
                    delay = min(delay * 2, 60.0)
                    continue
                return code, e.read()
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"    network err {e} on {path[:80]} — sleeping {delay:.1f}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        return 0, b"max retries exceeded"

    def list_conversations(self, page_size: int = 100):
        offset = 0
        while True:
            status, j = self.get(f"/backend-api/conversations?offset={offset}&limit={page_size}&order=updated")
            if status != 200:
                raise RuntimeError(f"list page off={offset} → {status}: {j!r}"[:300])
            items = j.get("items", [])
            for it in items:
                yield it
            if len(items) < page_size:
                return
            offset += page_size
            time.sleep(1.0)

    def get_conversation(self, cid: str) -> dict:
        status, j = self.get(f"/backend-api/conversation/{cid}")
        if status != 200:
            raise RuntimeError(f"conv {cid} → {status}: {j!r}"[:300])
        return j

    def file_download_url(self, file_id: str) -> str | None:
        status, j = self.get(f"/backend-api/files/{file_id}/download")
        if status != 200:
            return None
        return j.get("download_url")

    def fetch_bytes(self, url: str) -> bytes | None:
        # Signed S3-style URL — no auth needed.
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as e:
            print(f"    download err on {url[:80]}: {e}")
            return None


# ---------- conversation tree walk ----------

def walk_active_path(mapping: dict, current_node: str | None) -> list[dict]:
    path = []
    nid = current_node
    while nid:
        n = mapping.get(nid)
        if not n:
            break
        path.append(n)
        nid = n.get("parent")
    path.reverse()
    return [n for n in path if n.get("message")]


def has_alternate_branches(mapping: dict, active_path: list[dict]) -> bool:
    for n in active_path:
        pid = n.get("parent")
        if not pid:
            continue
        parent = mapping.get(pid)
        if parent and len(parent.get("children") or []) > 1:
            return True
    return False


# ---------- visibility filter ----------

def should_render(msg: dict) -> bool:
    if not msg:
        return False
    md = msg.get("metadata") or {}
    if md.get("is_visually_hidden_from_conversation"):
        return False
    content = msg.get("content") or {}
    ct = content.get("content_type")
    role = (msg.get("author") or {}).get("role")

    # System messages: only show if the user explicitly authored them; the rest
    # are memory injections and contextual-answers boilerplate.
    if role == "system":
        return False
    if ct == "model_editable_context":
        return False

    parts = content.get("parts") or []
    # An empty message with no parts and no attachments is dead weight.
    has_text = any(isinstance(p, str) and p.strip() for p in parts)
    has_dict = any(isinstance(p, dict) for p in parts)
    has_attachments = bool(md.get("attachments"))
    if not (has_text or has_dict or has_attachments or content.get("text")):
        return False
    return True


# ---------- markdown rendering ----------

SAFE_NAME = re.compile(r"[^\w\-. ]+", re.UNICODE)
# Windows reserves these names regardless of extension.
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}

# ChatGPT injects citation/search-reference markup using Unicode Private Use Area
# sentinels. The schema's metadata.citations / metadata.content_references is too
# degraded on older conversations ("Unsupported, please upgrade") to reconstruct
# real URLs, so we strip the markup and keep the visible text. Raw JSON in
# data/raw/ preserves everything for later forensic recovery.
#
#   U+E200 ... U+E201   cite reference block  (drop entirely — content is "cite|turn0searchN|...")
#   U+E203 ... U+E204   wraps a sentence quoted from search results  (drop markers, keep text)
#   U+E206              end-of-cluster marker  (drop)
#   U+E202, U+E205      separators inside the above  (drop)
_PUA_CITE_BLOCK = re.compile("[^]*")
_PUA_ANY = re.compile("[-]")


def clean_pua(s: str) -> str:
    s = _PUA_CITE_BLOCK.sub("", s)
    s = _PUA_ANY.sub("", s)
    return s

def slugify(title: str, max_len: int = 60) -> str:
    t = (title or "untitled").strip()
    t = re.sub(r"\s+", "-", t)
    t = SAFE_NAME.sub("", t)
    t = t.strip("-._")
    s = (t or "untitled")[:max_len]
    if s.upper().split(".", 1)[0] in _WIN_RESERVED:
        s = "_" + s
    return s


def fmt_ts(epoch: float | None) -> str:
    if not epoch:
        return ""
    import datetime
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat(timespec="seconds")


def asset_file_id(asset_pointer: str) -> str:
    # "sediment://file_XXX" or "file-service://file_XXX" → "file_XXX"
    return asset_pointer.split("//", 1)[-1]


def render_message(msg: dict, *, conv_files_dir: Path, conv_id8: str, client: Client | None, files_index: dict) -> str:
    """Render one visible message to markdown. Side effect: downloads attachments
    into conv_files_dir, populating files_index keyed by file_id."""
    role = (msg.get("author") or {}).get("role", "?")
    name = (msg.get("author") or {}).get("name")
    md = msg.get("metadata") or {}
    content = msg.get("content") or {}
    ct = content.get("content_type")
    recipient = msg.get("recipient")
    ts = fmt_ts(msg.get("create_time"))
    model = md.get("model_slug") or md.get("default_model_slug") or ""

    header_role = role.capitalize()
    if role == "tool" and name:
        header_role = f"Tool · {name}"
    if role == "assistant" and recipient and recipient != "all":
        header_role = f"Assistant → {recipient}"

    head = f"### {header_role}"
    meta_bits = []
    if model and role == "assistant":
        meta_bits.append(f"`{model}`")
    if ts:
        meta_bits.append(ts)
    if meta_bits:
        head += "  \n_" + " · ".join(meta_bits) + "_"

    body_parts: list[str] = []

    # Resolve body by content_type
    if ct == "code":
        # tool/code-interpreter cell
        lang = (content.get("language") or "").strip() or "text"
        text = content.get("text") or ""
        body_parts.append(f"```{lang}\n{text}\n```")
    elif ct in ("text", None):
        for p in content.get("parts") or []:
            if isinstance(p, str):
                body_parts.append(clean_pua(p))
            elif isinstance(p, dict):
                # rare in pure-text messages; fall through to multimodal handling
                body_parts.append(_render_part_dict(p, conv_files_dir, conv_id8, client, files_index))
        # some messages put body in content.text instead of parts
        if not body_parts and content.get("text"):
            body_parts.append(clean_pua(content["text"]))
    elif ct == "multimodal_text":
        for p in content.get("parts") or []:
            if isinstance(p, str):
                body_parts.append(clean_pua(p))
            elif isinstance(p, dict):
                body_parts.append(_render_part_dict(p, conv_files_dir, conv_id8, client, files_index))
    elif ct == "tether_browsing_display":
        body_parts.append("_(browsing display)_")
        if content.get("result"):
            body_parts.append("```\n" + content["result"][:2000] + "\n```")
    elif ct == "tether_quote":
        body_parts.append(f"> {content.get('text', '')}")
        if content.get("url"):
            body_parts.append(f"[source]({content['url']})")
    elif ct == "execution_output":
        body_parts.append("```\n" + (content.get("text") or "") + "\n```")
    else:
        # unknown — dump structure as a fenced json block so nothing is lost
        body_parts.append(f"<!-- unrendered content_type: {ct} -->")
        body_parts.append("```json\n" + json.dumps(content, indent=2)[:4000] + "\n```")

    # User-uploaded attachments (separate from parts)
    for att in md.get("attachments") or []:
        rel = _download_attachment(att.get("id"), att.get("name"), conv_files_dir, conv_id8, client, files_index, mime=att.get("mime_type"))
        if rel:
            if (att.get("mime_type") or "").startswith("image/"):
                body_parts.append(f"![{att.get('name','image')}]({rel})")
            else:
                size_kb = (att.get("size") or 0) / 1024
                body_parts.append(f"📎 [{att.get('name','file')}]({rel}) ({size_kb:.1f} KB, {att.get('mime_type','')})")

    body = "\n\n".join(b for b in body_parts if b is not None and str(b).strip())
    return head + "\n\n" + body


def _render_part_dict(p: dict, conv_files_dir: Path, conv_id8: str, client: Client | None, files_index: dict) -> str:
    """Render a non-string part (image_asset_pointer, etc)."""
    pct = p.get("content_type")
    if pct == "image_asset_pointer":
        ap = p.get("asset_pointer") or ""
        fid = asset_file_id(ap)
        rel = _download_attachment(fid, None, conv_files_dir, conv_id8, client, files_index, mime="image/")
        w, h = p.get("width"), p.get("height")
        dim = f" ({w}×{h})" if w and h else ""
        if rel:
            return f"![image{dim}]({rel})"
        return f"_(image {fid}{dim} — download failed)_"
    if pct == "audio_asset_pointer":
        ap = p.get("asset_pointer") or ""
        fid = asset_file_id(ap)
        rel = _download_attachment(fid, None, conv_files_dir, conv_id8, client, files_index, mime="audio/")
        return f"🎧 [audio]({rel})" if rel else f"_(audio {fid} — download failed)_"
    if pct == "video_asset_pointer":
        ap = p.get("asset_pointer") or ""
        fid = asset_file_id(ap)
        rel = _download_attachment(fid, None, conv_files_dir, conv_id8, client, files_index, mime="video/")
        return f"🎬 [video]({rel})" if rel else f"_(video {fid} — download failed)_"
    return f"<!-- unrendered part content_type: {pct} -->\n```json\n{json.dumps(p, indent=2)[:1000]}\n```"


def _download_attachment(file_id: str | None, name: str | None, conv_files_dir: Path, conv_id8: str, client: Client | None, files_index: dict, mime: str | None = None) -> str | None:
    """Download a file_id into conv_files_dir, return the markdown-relative path
    (e.g. ../files/abc12345/file_XXX_image.jpg). Returns None on failure.

    If client is None (e.g. --rerender mode), only files already on disk are
    linked — no API calls are attempted. ChatGPT auto-expires generated images
    after a retention window, so retrying unknown file_ids during rerender
    typically means thousands of slow per-file failures with nothing to gain."""
    if not file_id:
        return None
    if file_id in files_index:
        return files_index[file_id]
    safe = (name or "").replace("/", "_") if name else ""
    out_name = f"{file_id}_{safe}" if safe else file_id
    out_name = SAFE_NAME.sub("_", out_name)[:120]
    out = conv_files_dir / out_name
    if not out.exists():
        if client is None:
            return None
        conv_files_dir.mkdir(parents=True, exist_ok=True)
        url = client.file_download_url(file_id)
        if not url:
            return None
        data = client.fetch_bytes(url)
        if not data:
            return None
        out.write_bytes(data)
    rel = f"../files/{conv_id8}/{out.name}"
    files_index[file_id] = rel
    return rel


def render_conversation(j: dict, *, conv_files_dir: Path, conv_id8: str, client: Client | None) -> str:
    title = j.get("title") or "untitled"
    cid = j.get("conversation_id") or ""
    created = fmt_ts(j.get("create_time"))
    updated = fmt_ts(j.get("update_time"))
    model = j.get("default_model_slug") or ""
    # workspace_id is on list items but not on the per-conversation tree;
    # we stash it on the cached JSON under _workspace_id at fetch time.
    ws = j.get("_workspace_id") or j.get("workspace_id") or ""
    mapping = j.get("mapping") or {}
    cur = j.get("current_node")
    active = walk_active_path(mapping, cur)
    has_alt = has_alternate_branches(mapping, active)

    visible = [n for n in active if should_render(n["message"])]

    files_index: dict[str, str] = {}
    rendered = [render_message(n["message"], conv_files_dir=conv_files_dir, conv_id8=conv_id8, client=client, files_index=files_index) for n in visible]

    fm = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"id: {cid}",
        f"created: {created}",
        f"updated: {updated}",
        f"model: {model}",
        f"workspace: {ws}",
        f"messages_visible: {len(visible)}",
        f"messages_in_path: {len(active)}",
        f"nodes_in_tree: {len(mapping)}",
        f"has_alternate_branches: {str(has_alt).lower()}",
        "---",
    ]
    body = [f"# {title}", ""]
    if has_alt:
        body.append("> Note: this conversation has alternate (edited/regenerated) branches in the source tree. Only the active path is rendered here. Full tree preserved in `data/raw/{}.json`.\n".format(cid))
    body.extend(rendered)
    return "\n".join(fm) + "\n\n" + "\n\n".join(body) + "\n"


# ---------- orchestration ----------

def run(args):
    # --rerender works entirely from cached raw JSON; no cookies/network needed.
    # Handle it before anything that touches credentials so an expired cookie
    # doesn't block re-rendering an already-fetched archive.
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    if args.rerender:
        ids = sorted(p.stem for p in RAW_DIR.glob("*.json"))
        print(f"rerender mode: {len(ids)} cached conversations (no API calls)")
        # client=None makes _write_md skip attachment downloads — already-
        # downloaded files still link, missing ones render as "download failed".
        # Otherwise rerender re-attempts every previously failed file_id, which
        # on a typical run is ~1,500 slow API calls with nothing to gain.
        for n, cid in enumerate(ids if not args.limit else ids[: args.limit], 1):
            j = json.loads((RAW_DIR / f"{cid}.json").read_text(encoding="utf-8"))
            _write_md(j, cid, None)
            if n % 200 == 0:
                print(f"  rerendered {n}/{len(ids)}")
        print(f"  rerendered {len(ids)}/{len(ids)} — done")
        return

    cookie = load_creds()
    if args.check or args.refresh_token:
        token, user = mint_token(cookie)
        client = Client(cookie, token)
        print(f"  user        : {user.get('email')!r} ({user.get('name')!r})")
        print(f"  token length: {len(token)} bytes")
        if args.check:
            status, j = client.get("/backend-api/conversations?offset=0&limit=1&order=updated")
            print(f"  list status : HTTP {status}")
            if status == 200:
                print(f"  sample item : {j.get('items', [{}])[0].get('title','?')[:60]!r}")
            print("\ncreds look healthy. run without --check to start the export.")
        return
    token, user = mint_token(cookie)
    print(f"logged in as {user.get('email')!r} ({user.get('name')!r})")
    client = Client(cookie, token)

    print("listing conversations...")
    items = list(client.list_conversations())
    print(f"found {len(items)} conversations")
    if args.limit:
        items = items[: args.limit]
        print(f"limited to first {len(items)}")

    skipped = 0
    fetched = 0
    rendered = 0
    failed = []
    for i, it in enumerate(items, 1):
        cid = it["id"]
        raw_path = RAW_DIR / f"{cid}.json"
        existing_md = next(MD_DIR.glob(f"*_{cid[:8]}.md"), None)
        try:
            # Fast path: both raw and rendered already exist — fully skipped, no I/O.
            if raw_path.exists() and existing_md is not None:
                skipped += 1
                continue

            if raw_path.exists():
                # Raw cached but markdown missing — render only, no API call.
                j = json.loads(raw_path.read_text(encoding="utf-8"))
            else:
                # Need to fetch. Inject workspace_id from the list item — the
                # per-conversation endpoint doesn't return it, so we stash it
                # under _workspace_id so the rendered frontmatter and
                # --rerender both have it.
                j = client.get_conversation(cid)
                if it.get("workspace_id"):
                    j["_workspace_id"] = it["workspace_id"]
                raw_path.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
                fetched += 1
                time.sleep(1.0)

            _write_md(j, cid, client)
            rendered += 1
            tag = "render" if raw_path.exists() and not fetched else "fetch"
            print(f"  [{i:>4}/{len(items)}] {cid[:8]}  msgs={len(walk_active_path(j.get('mapping') or {}, j.get('current_node'))):>3}  {it.get('title','')[:50]}")
        except Exception as e:
            failed.append((cid, str(e)))
            print(f"  [{i:>4}/{len(items)}] {cid[:8]}  FAILED: {e}", flush=True)

    print(f"\ndone — fetched {fetched}, rendered {rendered}, skipped {skipped}, failed {len(failed)}")
    if failed:
        (DATA / "failures.json").write_text(json.dumps(failed, indent=2), encoding="utf-8")
        print(f"failures recorded in data/failures.json")


def _write_md(j: dict, cid: str, client: Client | None) -> Path:
    title = j.get("title") or "untitled"
    created = j.get("create_time")
    import datetime
    date = datetime.datetime.fromtimestamp(created or 0, tz=datetime.timezone.utc).strftime("%Y-%m-%d") if created else "unknown"
    cid8 = cid[:8]
    fname = f"{date}_{slugify(title)}_{cid8}.md"
    out = MD_DIR / fname
    conv_files_dir = FILES_DIR / cid8
    md = render_conversation(j, conv_files_dir=conv_files_dir, conv_id8=cid8, client=client)
    out.write_text(md, encoding="utf-8")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="only process the first N conversations (use for a trial run before the full export)")
    p.add_argument("--rerender", action="store_true", help="re-render markdown from cached raw JSON without re-fetching")
    p.add_argument("--check", action="store_true", help="verify cookies + access token + list endpoint, then exit")
    p.add_argument("--refresh-token", action="store_true", help="just mint a fresh access token from the session cookie")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
