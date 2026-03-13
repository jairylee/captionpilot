"""
Caption Pilot Admin API
FastAPI backend for managing Instagram automation accounts.
Port: 8766
"""
import os
import io
import sys
import json
import secrets
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response
import uvicorn

# ─── Paths ────────────────────────────────────────────────────────────────────
API_DIR = Path(__file__).parent.resolve()
WORKSPACE = API_DIR.parent.parent.resolve()
ACCOUNT_CONFIGS_DIR = WORKSPACE / "account_configs"
LOGS_DIR = WORKSPACE / "logs"
SCRIPTS_DIR = WORKSPACE / "scripts"
TOKEN_FILE = API_DIR / "admin_token.txt"

# ─── Album paths for thumbnails ───────────────────────────────────────────────
ALBUM_PATHS = {
    "mikescustomclassics": Path(
        "/Users/jairylee/Pictures/Photos Library.photoslibrary"
        "/scopes/cloudsharing/data/22804333864"
        "/17C6FBB1-E03B-452A-8D32-43DD5E6B36AB"
    ),
    "heartland_flower": Path(
        "/Users/jairylee/Pictures/Photos Library.photoslibrary"
        "/scopes/cloudsharing/data/22804333864"
        "/29B0E981-7286-4275-8E40-9BF2A8F0F329"
    ),
}

# ─── Audit logger (optional import — won't crash if missing) ─────────────────
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
try:
    from audit_logger import log_event, get_events as _get_audit_events
except ImportError:
    def log_event(account, event, data):  # noqa: E301
        pass
    def _get_audit_events(account, limit=100):  # noqa: E301
        return []

# ─── Token auth ───────────────────────────────────────────────────────────────
def get_or_create_token() -> str:
    if not TOKEN_FILE.exists():
        token = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(token)
        print(f"[admin] Generated new admin token → {TOKEN_FILE}")
    return TOKEN_FILE.read_text().strip()

ADMIN_TOKEN = get_or_create_token()

# ─── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="Caption Pilot Admin API", version="1.0.0")

# Serve admin console at /admin
ADMIN_DIR = API_DIR.parent / "admin"
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")

@app.get("/")
def root():
    return RedirectResponse(url="/admin/")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://captionpilot.app",
        "https://www.captionpilot.app",
        "https://api.captionpilot.app",
        "http://localhost:8766",
        "http://localhost:3000",
        "http://127.0.0.1:8766",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# ─── Helpers ──────────────────────────────────────────────────────────────────
def read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_account_names() -> list[str]:
    if not ACCOUNT_CONFIGS_DIR.exists():
        return []
    return sorted(f.stem for f in ACCOUNT_CONFIGS_DIR.glob("*.json"))


def get_state_path(account: str) -> Path:
    return WORKSPACE / f".state-{account}.json"


def get_selection_state_path() -> Path:
    return WORKSPACE / ".photo-selection-state.json"


def get_enhancement_state_path(account: str) -> Path:
    return WORKSPACE / f".enhancement-state-{account}.json"


def tail_file(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.splitlines()[-n:]
    except Exception:
        return []


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Pipeline script map (account name → script filename)
PIPELINE_SCRIPTS = {
    "mikescustomclassics": "instagram_post.py",
    "heartland_flower": "heartland_propose.py",
}


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "timestamp": utcnow(),
        "workspace": str(WORKSPACE),
        "accounts": get_account_names(),
    }


@app.get("/api/accounts")
def list_accounts(auth=Depends(verify_token)):
    accounts = []
    for name in get_account_names():
        config = read_json(ACCOUNT_CONFIGS_DIR / f"{name}.json")
        state = read_json(get_state_path(name))
        files = state.get("files", {})
        pending = sum(1 for f in files.values() if f.get("status") == "pending")
        posted = sum(1 for f in files.values() if f.get("status") == "posted")
        # Latest post timestamp + filename
        last_post = None
        last_post_file = None
        for fname, f in files.items():
            ts = f.get("posted_at")
            if ts and (last_post is None or ts > last_post):
                last_post = ts
                last_post_file = fname
        # Selection state
        sel_state = read_json(get_selection_state_path())
        sel = sel_state.get(name, {})
        accounts.append({
            "name": name,
            "display_name": config.get("display_name", name),
            "instagram_handle": config.get("instagram_handle", ""),
            "niche": config.get("niche", ""),
            "location": config.get("location", ""),
            "pending_count": pending,
            "posted_count": posted,
            "last_post_date": last_post,
            "last_post_file": last_post_file,
            "selection_status": sel.get("status"),
        })
    return accounts


@app.get("/api/accounts/{account}")
def get_account(account: str, auth=Depends(verify_token)):
    path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account not found")
    return read_json(path)


@app.put("/api/accounts/{account}")
async def update_account(account: str, request: Request, auth=Depends(verify_token)):
    path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    write_json(path, data)
    return {"ok": True, "account": account}


@app.get("/api/accounts/{account}/state")
def get_account_state(account: str, auth=Depends(verify_token)):
    state_path = get_state_path(account)
    if state_path.exists():
        state = read_json(state_path)
        return {"source": "v2", "data": state}
    # Fall back to legacy
    legacy = read_json(WORKSPACE / ".instagram-state.json")
    return {"source": "legacy", "data": legacy}


@app.put("/api/accounts/{account}/state/{filename:path}")
async def update_file_status(account: str, filename: str, request: Request, auth=Depends(verify_token)):
    path = get_state_path(account)
    state = read_json(path)
    if not state:
        raise HTTPException(status_code=404, detail="State file not found")
    files = state.get("files", {})
    if filename not in files:
        raise HTTPException(status_code=404, detail=f"File '{filename}' not in state")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    status = body.get("status")
    if status not in ("posted", "pending", "skipped"):
        raise HTTPException(status_code=400, detail="status must be: posted | pending | skipped")
    files[filename]["status"] = status
    if status == "posted" and not files[filename].get("posted_at"):
        files[filename]["posted_at"] = utcnow()
    state["files"] = files
    write_json(path, state)
    return {"ok": True, "file": filename, "status": status}


@app.post("/api/accounts/{account}/pipeline/run")
def run_pipeline(account: str, auth=Depends(verify_token)):
    config = read_json(ACCOUNT_CONFIGS_DIR / f"{account}.json")
    if not config:
        raise HTTPException(status_code=404, detail="Account not found")

    script_name = PIPELINE_SCRIPTS.get(account, "instagram_post.py")
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        script_name = "instagram_post.py"
        script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        return {"ok": False, "error": f"Script not found: {script_name}"}

    try:
        result = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKSPACE),
        )
        combined = (result.stdout + "\n" + result.stderr).strip()
        return {
            "ok": True,
            "return_code": result.returncode,
            "output": combined.splitlines()[-50:],
            "script": script_name,
            "ran_at": utcnow(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Script timed out after 120 seconds"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/accounts/{account}/posts")
def get_account_posts(account: str, auth=Depends(verify_token)):
    state = read_json(get_state_path(account))
    posts = []
    if state:
        for fname, fdata in state.get("files", {}).items():
            if fdata.get("status") == "posted":
                posts.append({
                    "file": fname,
                    "posted_at": fdata.get("posted_at"),
                    "post_url": fdata.get("post_url"),
                    "post_method": fdata.get("post_method"),
                    "notes": fdata.get("notes", ""),
                    "phash": fdata.get("phash"),
                })
    # Legacy batches are MCC-only — don't pollute other accounts
    batches = []
    if account == "mikescustomclassics":
        legacy = read_json(WORKSPACE / ".instagram-state.json")
        batches = legacy.get("posted_batches", [])
    return {"posts": posts, "batches": batches}


@app.get("/api/cron")
def get_cron(auth=Depends(verify_token)):
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [l for l in result.stdout.splitlines()]
        return {"lines": lines, "raw": result.stdout}
    except Exception as e:
        return {"lines": [], "error": str(e)}


@app.get("/api/logs")
def list_logs(auth=Depends(verify_token)):
    if not LOGS_DIR.exists():
        return []
    return sorted(f.stem for f in LOGS_DIR.glob("*.log"))


@app.get("/api/logs/{name}")
def get_log(name: str, n: int = 200, auth=Depends(verify_token)):
    # Security: prevent path traversal
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid log name")
    path = LOGS_DIR / f"{name}.log"
    lines = tail_file(path, n)
    return {"name": name, "lines": lines, "total": len(lines), "path": str(path)}


@app.get("/api/pending/selections")
def get_pending_selections(auth=Depends(verify_token)):
    return read_json(get_selection_state_path())


@app.get("/api/pending/selections/refresh")
def refresh_pending_selections(auth=Depends(verify_token)):
    """Reconcile stale pending state and return refreshed data."""
    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    tokens = _load_tokens()
    changed = False

    for account, acct_state in sel_state.items():
        if acct_state.get("status") != "pending":
            continue

        # If any token for this account has been used → mark completed
        for tok_meta in tokens.values():
            if tok_meta.get("account") == account and tok_meta.get("used"):
                acct_state["status"] = "completed"
                acct_state["reconciled_at"] = utcnow()
                changed = True
                break

        # If still pending, check whether all candidates are skipped/posted in state manager
        if acct_state.get("status") == "pending":
            try:
                sys.path.insert(0, str(WORKSPACE / "scripts"))
                from state_manager import StateManager  # type: ignore
                sm = StateManager(account)
                scores = acct_state.get("scores", [])
                if scores and all(
                    sm.is_posted(Path(s.get("path", "")).name) for s in scores
                ):
                    acct_state["status"] = "stale"
                    acct_state["reconciled_at"] = utcnow()
                    changed = True
            except Exception:
                pass

        # Fallback: check whether all candidate files are gone from disk
        if acct_state.get("status") == "pending":
            scores = acct_state.get("scores", [])
            if scores and all(not Path(s.get("path", "")).exists() for s in scores):
                acct_state["status"] = "stale"
                acct_state["reconciled_at"] = utcnow()
                changed = True

    if changed:
        write_json(sel_path, sel_state)

    # Clip selected indices to valid range before returning (stale state guard)
    for acct_state in sel_state.values():
        scores = acct_state.get("scores", [])
        if scores and "selected" in acct_state:
            acct_state["selected"] = [i for i in acct_state["selected"] if i < len(scores)]

    return sel_state


@app.delete("/api/pending/selections/{account}")
def clear_selection_state(account: str, auth=Depends(verify_token)):
    """Remove stale selection state for a specific account."""
    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    if account not in sel_state:
        raise HTTPException(status_code=404, detail=f"No selection state for '{account}'")
    del sel_state[account]
    write_json(sel_path, sel_state)
    return {"ok": True, "removed": account}


@app.post("/api/pending/selections/{account}")
async def update_selection(account: str, request: Request, auth=Depends(verify_token)):
    path = get_selection_state_path()
    state = read_json(path)
    if account not in state:
        raise HTTPException(status_code=404, detail=f"No selection state for account '{account}'")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    action = body.get("action")  # post | swap | add | remove
    photo_num = body.get("photo_num")  # 0-indexed into scores array

    acct_state = state[account]
    selected = list(acct_state.get("selected", []))
    scores = acct_state.get("scores", [])

    if action == "post":
        acct_state["status"] = "approved"
        acct_state["approved_at"] = utcnow()
    elif action == "remove" and photo_num is not None:
        if photo_num in selected:
            selected.remove(photo_num)
        acct_state["selected"] = selected
    elif action == "add" and photo_num is not None:
        if photo_num not in selected and photo_num < len(scores):
            selected.append(photo_num)
        acct_state["selected"] = selected
    elif action == "swap" and photo_num is not None:
        # Replace the last (lowest-scored) selected photo
        if selected and photo_num not in selected and photo_num < len(scores):
            selected[-1] = photo_num
        acct_state["selected"] = selected
    else:
        raise HTTPException(status_code=400, detail="action must be: post | swap | add | remove")

    state[account] = acct_state
    write_json(path, state)
    return {"ok": True, "state": acct_state}


@app.get("/api/feed/sync/{account}")
def sync_feed(account: str, auth=Depends(verify_token)):
    script_path = SCRIPTS_DIR / "state_manager.py"
    if not script_path.exists():
        raise HTTPException(status_code=500, detail="state_manager.py not found")
    try:
        result = subprocess.run(
            ["python3", str(script_path), "--sync-feed", account],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(WORKSPACE),
        )
        combined = (result.stdout + "\n" + result.stderr).strip()
        return {
            "ok": result.returncode == 0,
            "output": combined.splitlines()[-50:],
            "ran_at": utcnow(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Sync timed out after 60 seconds"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/accounts/{account}/enhancement")
def get_enhancement_state(account: str, auth=Depends(verify_token)):
    return read_json(get_enhancement_state_path(account))


@app.get("/api/thumbnails/{account}/{filename:path}")
def serve_thumbnail(account: str, filename: str):  # no auth — localhost only, img tags can't send headers
    """Return a 120×120 JPEG thumbnail for a media file."""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    album_path = ALBUM_PATHS.get(account)
    if not album_path:
        raise HTTPException(status_code=404, detail=f"No album path configured for '{account}'")

    file_path = album_path / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        from PIL import Image
        img = Image.open(str(file_path))
        img.thumbnail((120, 120))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="image/jpeg",
            headers={"Cache-Control": "max-age=86400"},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="Pillow (PIL) not installed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Thumbnail error: {e}")


# ─── Approval token system ────────────────────────────────────────────────────
import hashlib
import secrets as _secrets

APPROVAL_TOKENS_FILE = WORKSPACE / ".approval-tokens.json"

def _load_tokens() -> dict:
    if APPROVAL_TOKENS_FILE.exists():
        try:
            return json.loads(APPROVAL_TOKENS_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_tokens(tokens: dict):
    APPROVAL_TOKENS_FILE.write_text(json.dumps(tokens, indent=2))

@app.get("/api/approvals/history")
def get_approvals_history():  # no auth — img tags in frontend can't send headers
    """Return the most recent 20 approval token entries, enriched with selection state."""
    tokens = _load_tokens()
    sel_state = read_json(get_selection_state_path())

    entries = []
    for token, meta in tokens.items():
        account = meta.get("account", "")
        acct_sel = sel_state.get(account, {})
        scores = acct_sel.get("scores", [])
        selected = acct_sel.get("selected", [])

        entry = {
            "token": token,
            "token_prefix": token[:8] + "…",
            "account": account,
            "created_at": meta.get("created_at"),
            "used": meta.get("used", False),
            "used_at": meta.get("used_at"),
            "invalidated": meta.get("invalidated", False),
            "invalidated_reason": meta.get("invalidated_reason"),
            "action": meta.get("action"),
            "photos": [
                {
                    "index": i,
                    "name": s.get("name", ""),
                    "score": s.get("score"),
                    "selected": i in selected,
                    # Use thumbnail endpoint (stable, no token expiry)
                    "thumb_url": f"http://localhost:8766/api/thumbnails/{account}/{s.get('name', '')}",
                }
                for i, s in enumerate(scores)
            ],
        }
        entries.append(entry)

    # Sort by created_at desc, take most recent 20
    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return entries[:20]


@app.post("/api/approve/generate/{account}")
def generate_approval_token(account: str, auth=Depends(verify_token)):
    """Generate a one-time approval token for an account's pending selection."""
    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    if account not in sel_state:
        raise HTTPException(status_code=404, detail=f"No pending selection for '{account}'")

    token = _secrets.token_urlsafe(24)
    tokens = _load_tokens()
    tokens[token] = {
        "account": account,
        "created_at": utcnow(),
        "used": False,
    }
    _save_tokens(tokens)
    url = f"https://captionpilot.app/approve?t={token}"

    # Include scheduled_time from account config (default "6:00 PM CDT")
    cfg = read_json(ACCOUNT_CONFIGS_DIR / f"{account}.json")
    scheduled_time = cfg.get("post_time", "6:00 PM CDT")

    log_event(account, "approval_sent", {"url": url, "token": token[:8] + "…"})

    return {"token": token, "url": url, "account": account, "scheduled_time": scheduled_time}

@app.get("/api/approve/{token}")
def get_approval(token: str):
    """Public endpoint — returns selection data for the approval page. No admin auth needed."""
    tokens = _load_tokens()
    if token not in tokens:
        raise HTTPException(status_code=404, detail="Invalid or expired approval token")
    meta = tokens[token]
    if meta.get("used"):
        raise HTTPException(status_code=410, detail="This approval link has already been used")

    account = meta["account"]
    log_event(account, "approval_opened", {"token": token[:8] + "…"})
    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    if account not in sel_state:
        raise HTTPException(status_code=404, detail="No pending selection found")

    acct_state = sel_state[account]
    # Add photo URLs for each scored photo
    scores = acct_state.get("scores", [])
    for i, s in enumerate(scores):
        s["photo_url"] = f"https://api.captionpilot.app/api/photos/{token}/{i}"
        s["index"] = i

    # Load account config for display name
    cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    cfg = read_json(cfg_path)

    return {
        "account": account,
        "display_name": cfg.get("display_name", account),
        "instagram_handle": cfg.get("instagram_handle", f"@{account}"),
        "status": acct_state.get("status"),
        # Clip selected indices to valid range — stale state can have out-of-bounds indices
        "selected": [i for i in acct_state.get("selected", []) if i < len(scores)],
        "scores": scores,
        "token": token,
    }

@app.get("/api/photos/{token}/{index}")
def serve_photo(token: str, index: int):
    """Serve a photo file for the approval page. Token-gated.
    Allows used tokens so the history view can still display photos."""
    from fastapi.responses import FileResponse
    tokens = _load_tokens()
    if token not in tokens:
        raise HTTPException(status_code=403, detail="Invalid token")
    # Note: used tokens are allowed here so history view still works

    account = tokens[token]["account"]
    sel_state = read_json(get_selection_state_path())
    if account not in sel_state:
        raise HTTPException(status_code=404, detail="No selection state")

    scores = sel_state[account].get("scores", [])
    if index < 0 or index >= len(scores):
        raise HTTPException(status_code=404, detail="Photo index out of range")

    photo_path = Path(scores[index]["path"])
    if not photo_path.exists():
        raise HTTPException(status_code=404, detail=f"Photo file not found: {photo_path.name}")

    return FileResponse(str(photo_path), media_type="image/jpeg")

@app.post("/api/approve/{token}/confirm")
async def confirm_approval(token: str, request: Request):
    """Public endpoint — processes the approval. No admin auth needed."""
    tokens = _load_tokens()
    if token not in tokens:
        raise HTTPException(status_code=404, detail="Invalid or expired approval token")
    meta = tokens[token]
    if meta.get("used"):
        raise HTTPException(status_code=410, detail="This approval link has already been used")

    account = meta["account"]

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    action = body.get("action", "post")  # preview_caption | post | schedule | swap | add | remove | skip_unselected
    photo_num = body.get("photo_num")
    selected = body.get("selected")  # full list of selected indices (from page)

    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    if account not in sel_state:
        raise HTTPException(status_code=404, detail="No pending selection")

    acct_state = sel_state[account]

    if action == "preview_caption":
        # Generate caption for the current selection without posting — returns caption text
        if selected is not None:
            acct_state["selected"] = selected
        scores = acct_state.get("scores", [])
        sel_indices = acct_state.get("selected", list(range(len(scores))))
        sel_paths = [scores[i]["path"] for i in sel_indices if i < len(scores)]
        media_items = [{"staged_path": p, "filename": Path(p).name, "comment": None}
                       for p in sel_paths if Path(p).exists()]
        try:
            sys.path.insert(0, str(WORKSPACE / "scripts"))
            cfg_path = WORKSPACE / "account_configs" / f"{account}.json"
            cfg = read_json(cfg_path) or {}
            cfg.setdefault("account", account)
            from instagram_post import generate_caption  # type: ignore
            caption = generate_caption(media_items, cfg)
        except Exception as e:
            caption = f"[Caption generation failed: {e}]"
        return {"caption": caption, "photo_count": len(media_items)}

    elif action == "schedule":
        # Save selection state, mark scheduled — do NOT spawn instagram_post.py
        if selected is not None:
            acct_state["selected"] = selected
        hero_index = body.get("hero_index")
        if hero_index is not None:
            acct_state["hero_index"] = hero_index
        # Store user-reviewed caption if provided
        approved_caption = body.get("caption")
        if approved_caption:
            acct_state["approved_caption"] = approved_caption
        acct_state["status"] = "scheduled"
        acct_state["approved_at"] = utcnow()
        acct_state["approved_via"] = "web"
        sel_state[account] = acct_state
        write_json(sel_path, sel_state)

        # Mark token used
        meta["used"] = True
        meta["used_at"] = utcnow()
        tokens[token] = meta
        _save_tokens(tokens)

        log_event(account, "post_scheduled", {
            "selected": acct_state.get("selected", []),
            "hero_index": acct_state.get("hero_index"),
        })

        return {"ok": True, "message": "Scheduled for 6 PM."}

    elif action == "post":
        # Accept whatever selected list the page sends
        if selected is not None:
            acct_state["selected"] = selected
        hero_index = body.get("hero_index")
        if hero_index is not None:
            acct_state["hero_index"] = hero_index
        acct_state["status"] = "approved"
        acct_state["approved_at"] = utcnow()
        acct_state["approved_via"] = "web"
        sel_state[account] = acct_state
        write_json(sel_path, sel_state)

        # Mark token used
        meta["used"] = True
        meta["used_at"] = utcnow()
        tokens[token] = meta
        _save_tokens(tokens)

        log_event(account, "photos_selected", {
            "selected": acct_state.get("selected", []),
            "hero_index": acct_state.get("hero_index"),
            "count": len(acct_state.get("selected", [])),
        })

        # Trigger the pipeline — synchronous so mark_posted() runs before we return
        script_path = WORKSPACE / "scripts" / "instagram_post.py"
        cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
        pipeline_ok = False
        try:
            result = subprocess.run(
                ["python3", str(script_path), "--account", account, "--config", str(cfg_path)],
                cwd=str(WORKSPACE),
                timeout=120,
                capture_output=True,
                text=True,
            )
            pipeline_ok = result.returncode == 0
            if result.returncode != 0:
                print(f"[admin] instagram_post.py exited {result.returncode} for {account}")
                print(f"[admin] stdout: {result.stdout[-2000:]}")
                print(f"[admin] stderr: {result.stderr[-2000:]}")
            else:
                print(f"[admin] instagram_post.py succeeded for {account}")
                print(f"[admin] output: {result.stdout[-1000:]}")
        except subprocess.TimeoutExpired:
            print(f"[admin] instagram_post.py timed out for {account} — state will reconcile on next sync")
        except Exception as e:
            print(f"[admin] instagram_post.py launch error for {account}: {e}")

        log_event(account, "post_completed" if pipeline_ok else "post_skipped", {
            "pipeline_ok": pipeline_ok,
        })

        return {"ok": True, "message": "Approved! Post is being published now."}

    elif action == "skip_unselected":
        skip_indices = body.get("skip_indices", [])
        scores = acct_state.get("scores", [])
        # Mark those files as skipped in the state manager
        script_path = SCRIPTS_DIR / "state_manager.py"
        for idx in skip_indices:
            if 0 <= idx < len(scores):
                filename = scores[idx]["name"]
                try:
                    subprocess.run(
                        ["python3", "-c",
                         f"import sys; sys.path.insert(0,'scripts'); from state_manager import StateManager; "
                         f"mgr = StateManager('{account}'); mgr.mark_skipped('{filename}', reason='skipped on approval page')"],
                        cwd=str(WORKSPACE), timeout=10, capture_output=True
                    )
                except Exception:
                    pass
        return {"ok": True, "skipped": len(skip_indices)}

    elif action in ("swap", "add", "remove") and photo_num is not None:
        cur_selected = list(acct_state.get("selected", []))
        scores = acct_state.get("scores", [])
        if action == "remove" and photo_num in cur_selected:
            cur_selected.remove(photo_num)
        elif action == "add" and photo_num not in cur_selected and photo_num < len(scores):
            cur_selected.append(photo_num)
        elif action == "swap" and cur_selected and photo_num not in cur_selected:
            cur_selected[-1] = photo_num
        acct_state["selected"] = cur_selected
        sel_state[account] = acct_state
        write_json(sel_path, sel_state)
        return {"ok": True, "selected": cur_selected}

    else:
        raise HTTPException(status_code=400, detail="action must be: post | swap | add | remove")


# ─── Video approval token system ─────────────────────────────────────────────

VIDEO_APPROVAL_TOKENS_FILE = WORKSPACE / ".video-approval-tokens.json"


def _load_video_tokens() -> dict:
    if VIDEO_APPROVAL_TOKENS_FILE.exists():
        try:
            return json.loads(VIDEO_APPROVAL_TOKENS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_video_tokens(tokens: dict):
    VIDEO_APPROVAL_TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


@app.post("/api/video-approve/generate/{account}")
def generate_video_approval_token(account: str, auth=Depends(verify_token)):
    """Generate a one-time video approval token."""
    video_queue_file = WORKSPACE / f".video-queue-{account}.json"
    if not video_queue_file.exists():
        raise HTTPException(status_code=404, detail=f"No pending video for '{account}'")

    video_data = read_json(video_queue_file)
    token = _secrets.token_urlsafe(24)
    tokens = _load_video_tokens()
    tokens[token] = {
        "account": account,
        "created_at": utcnow(),
        "used": False,
        "video_path": video_data.get("video_path", ""),
        "poster_path": video_data.get("poster_path", ""),
        "caption": video_data.get("caption", ""),
        "duration": video_data.get("duration", 0),
    }
    _save_video_tokens(tokens)
    url = f"https://captionpilot.app/video-approve?t={token}"
    return {"token": token, "url": url, "account": account}


@app.get("/api/video-approve/{token}")
def get_video_approval(token: str):
    """Public endpoint — returns video info for the approval page."""
    tokens = _load_video_tokens()
    if token not in tokens:
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    meta = tokens[token]
    if meta.get("used"):
        raise HTTPException(status_code=410, detail="This approval link has already been used")

    account = meta["account"]
    cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    cfg = read_json(cfg_path)

    return {
        "account": account,
        "display_name": cfg.get("display_name", account),
        "instagram_handle": cfg.get("instagram_handle", f"@{account}"),
        "video_path": meta.get("video_path", ""),
        "poster_url": f"https://api.captionpilot.app/api/video-approve/{token}/poster",
        "caption": meta.get("caption", ""),
        "duration": meta.get("duration", 0),
        "token": token,
    }


@app.get("/api/video-approve/{token}/poster")
def serve_video_poster(token: str):
    """Serve the video poster frame."""
    from fastapi.responses import FileResponse
    tokens = _load_video_tokens()
    if token not in tokens:
        raise HTTPException(status_code=403, detail="Invalid token")
    if tokens[token].get("used"):
        raise HTTPException(status_code=410, detail="Token expired")

    poster_path = Path(tokens[token].get("poster_path", ""))
    if not poster_path.exists():
        # Try to generate a poster from the video using ffmpeg
        video_path = Path(tokens[token].get("video_path", ""))
        if video_path.exists():
            import subprocess as sp
            poster_path = video_path.with_suffix(".poster.jpg")
            sp.run(
                ["ffmpeg", "-i", str(video_path), "-vframes", "1", "-q:v", "2",
                 str(poster_path), "-y"],
                capture_output=True, timeout=30,
            )
        if not poster_path.exists():
            raise HTTPException(status_code=404, detail="Poster not available")

    return FileResponse(str(poster_path), media_type="image/jpeg")


@app.post("/api/video-approve/{token}/confirm")
async def confirm_video_approval(token: str, request: Request):
    """Public endpoint — processes the video approval decision."""
    tokens = _load_video_tokens()
    if token not in tokens:
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    meta = tokens[token]
    if meta.get("used"):
        raise HTTPException(status_code=410, detail="Already used")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    action = body.get("action")  # "reel" | "feed" | "skip"
    if action not in ("reel", "feed", "skip"):
        raise HTTPException(status_code=400, detail="action must be: reel | feed | skip")

    account = meta["account"]

    # Mark token used
    meta["used"] = True
    meta["used_at"] = utcnow()
    meta["decision"] = action
    tokens[token] = meta
    _save_video_tokens(tokens)

    if action == "skip":
        video_queue_file = WORKSPACE / f".video-queue-{account}.json"
        if video_queue_file.exists():
            qdata = read_json(video_queue_file)
            qdata["decision"] = "skip"
            qdata["decided_at"] = utcnow()
            write_json(video_queue_file, qdata)
        return {"ok": True, "message": "Video skipped."}

    # reel or feed — write decision to queue file and trigger pipeline
    video_queue_file = WORKSPACE / f".video-queue-{account}.json"
    if video_queue_file.exists():
        qdata = read_json(video_queue_file)
        qdata["decision"] = action
        qdata["decided_at"] = utcnow()
        write_json(video_queue_file, qdata)

    # Trigger video_decision_watcher to pick up the decision
    script_path = SCRIPTS_DIR / "video_decision_watcher.py"
    cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    try:
        result = subprocess.run(
            ["python3", str(script_path), "--account", account, "--config", str(cfg_path),
             "--process-decision"],
            cwd=str(WORKSPACE),
            timeout=120,
            capture_output=True,
            text=True,
        )
        print(f"[video] Decision watcher output: {result.stdout[:200]}")
    except Exception as e:
        print(f"[video] Warning: could not trigger watcher: {e}")

    msg = "Publishing as a Reel!" if action == "reel" else "Publishing as a Feed video!"
    return {"ok": True, "message": msg}


# ─── Conversation endpoint ────────────────────────────────────────────────────

BB_SERVER = "http://localhost:1234"   # local BB — no tunnel rotation issues
BB_PASSWORD = "2JDT8bGV5IJ6"


def _audit_event_to_message(ev: dict) -> "dict | None":
    event = ev.get("event", "")
    ts = ev.get("ts", "")
    data = ev.get("data", {})
    if not ts:
        return None
    if event == "approval_sent":
        url = data.get("url", "")
        text = f"📸 Sent approval link: {url}" if url else "📸 Sent approval link"
        return {"ts": ts, "kind": "outbound", "text": text, "source": "audit", "event_type": event}
    elif event == "approval_opened":
        return {"ts": ts, "kind": "system", "text": "Link opened", "source": "audit", "event_type": event}
    elif event == "photos_selected":
        count = data.get("count", len(data.get("selected", [])))
        return {"ts": ts, "kind": "system", "text": f"{count} photos selected", "source": "audit", "event_type": event}
    elif event == "post_scheduled":
        return {"ts": ts, "kind": "system", "text": "Scheduled for 6 PM", "source": "audit", "event_type": event}
    elif event == "post_completed":
        url = data.get("post_url", data.get("url", ""))
        text = f"✅ Posted to Instagram: {url}" if url else "✅ Posted to Instagram"
        return {"ts": ts, "kind": "system", "text": text, "source": "audit", "event_type": event}
    elif event == "post_skipped":
        return {"ts": ts, "kind": "system", "text": "⏭ Post skipped", "source": "audit", "event_type": event}
    elif event == "video_decision":
        decision = data.get("decision", data.get("action", ""))
        return {"ts": ts, "kind": "system", "text": f"🎬 Video: {decision}", "source": "audit", "event_type": event}
    return None


def _fetch_bb_messages(handle: str) -> list:
    """Fetch iMessages for a handle via local BlueBubbles REST API (POST endpoints)."""
    import urllib.request
    from datetime import timedelta
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        def bb_post(path: str, body: dict) -> dict:
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{BB_SERVER}{path}?password={BB_PASSWORD}",
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "CaptionPilot/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        # Find the chat GUID for this handle (try exact match first, then phone/email normalisation)
        chats_data = bb_post("/api/v1/chat/query", {"limit": 50, "offset": 0})
        chats = chats_data.get("data", [])

        target_guid = None
        handle_lower = handle.lower().strip()
        # Strip spaces/dashes from phone numbers for comparison
        handle_norm = "".join(c for c in handle_lower if c.isdigit() or c in "@.+")
        for chat in chats:
            for p in chat.get("participants", []):
                addr = p.get("address", "").lower().strip()
                addr_norm = "".join(c for c in addr if c.isdigit() or c in "@.+")
                if addr_lower := addr:
                    if addr_lower == handle_lower or addr_norm == handle_norm:
                        target_guid = chat.get("guid")
                        break
            if target_guid:
                break

        if not target_guid:
            # Fall back: construct GUID directly
            target_guid = f"iMessage;-;{handle}"

        # Fetch messages via query endpoint
        msgs_data = bb_post("/api/v1/message/query", {
            "chatGuid": target_guid,
            "limit": 75,
            "sort": "DESC",
            "after": int(cutoff.timestamp() * 1000),
        })

        messages = []
        for msg in msgs_data.get("data", []):
            date_val = msg.get("dateCreated") or msg.get("date")
            if date_val is None:
                continue
            if isinstance(date_val, (int, float)):
                ts_dt = datetime.fromtimestamp(date_val / 1000, tz=timezone.utc)
            else:
                try:
                    ts_dt = datetime.fromisoformat(str(date_val).replace("Z", "+00:00"))
                except Exception:
                    continue
            if ts_dt < cutoff:
                continue
            text = (msg.get("text") or "").strip()
            # Skip empty/attachment-only messages
            if not text and not msg.get("attachments"):
                continue
            if not text and msg.get("attachments"):
                text = f"[Attachment: {msg['attachments'][0].get('mimeType','file')}]"
            is_from_me = msg.get("isFromMe", False)
            messages.append({
                "ts": ts_dt.isoformat(),
                "kind": "outbound" if is_from_me else "inbound",
                "text": text,
                "source": "bluebubbles",
                "event_type": None,
            })
        return messages
    except Exception as e:
        print(f"[conversation] BlueBubbles fetch failed for handle={handle}: {e}")
        return []


@app.get("/api/conversation/{account}")
def get_conversation(account: str, auth=Depends(verify_token)):
    """Return unified iMessage-style conversation thread for an account."""
    cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
    if not cfg_path.exists():
        raise HTTPException(status_code=404, detail="Account not found")
    cfg = read_json(cfg_path)
    handle = cfg.get("imessage_handle", "")

    messages = []

    # 1. Read audit log
    audit_path = WORKSPACE / f".audit-{account}.jsonl"
    if audit_path.exists():
        try:
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    msg = _audit_event_to_message(ev)
                    if msg:
                        messages.append(msg)
                except Exception:
                    pass
        except Exception:
            pass

    # 2. Fetch BlueBubbles messages (graceful degradation if unavailable)
    if handle:
        bb_msgs = _fetch_bb_messages(handle)
        messages.extend(bb_msgs)

    # Sort by ts ascending
    messages.sort(key=lambda m: m.get("ts", ""))

    return {"messages": messages, "account": account, "handle": handle}


# ─── Audit log endpoint ───────────────────────────────────────────────────────

@app.get("/api/audit/{account}")
def get_audit_log(account: str, limit: int = 100, auth=Depends(verify_token)):
    """Return last N audit events for an account."""
    events = _get_audit_events(account, limit)
    return {"account": account, "events": events, "count": len(events)}


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[admin] Workspace: {WORKSPACE}")
    print(f"[admin] Starting Caption Pilot Admin API on http://0.0.0.0:8766")
    print(f"[admin] Admin token: {ADMIN_TOKEN}")
    uvicorn.run(app, host="0.0.0.0", port=8766, log_level="info")
