"""
Caption Pilot Admin API
FastAPI backend for managing Instagram automation accounts.
Port: 8766
"""
import os
import json
import secrets
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ─── Paths ────────────────────────────────────────────────────────────────────
API_DIR = Path(__file__).parent.resolve()
WORKSPACE = API_DIR.parent.parent.resolve()
ACCOUNT_CONFIGS_DIR = WORKSPACE / "account_configs"
LOGS_DIR = WORKSPACE / "logs"
SCRIPTS_DIR = WORKSPACE / "scripts"
TOKEN_FILE = API_DIR / "admin_token.txt"

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        # Latest post timestamp
        last_post = None
        for f in files.values():
            ts = f.get("posted_at")
            if ts and (last_post is None or ts > last_post):
                last_post = ts
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
    # Include posted_batches from legacy state
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
    return {"token": token, "url": url, "account": account}

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
        "selected": acct_state.get("selected", []),
        "scores": scores,
        "token": token,
    }

@app.get("/api/photos/{token}/{index}")
def serve_photo(token: str, index: int):
    """Serve a photo file for the approval page. Token-gated."""
    from fastapi.responses import FileResponse
    tokens = _load_tokens()
    if token not in tokens:
        raise HTTPException(status_code=403, detail="Invalid token")
    if tokens[token].get("used"):
        raise HTTPException(status_code=410, detail="Token expired")

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

    action = body.get("action", "post")  # post | swap | add | remove
    photo_num = body.get("photo_num")
    selected = body.get("selected")  # full list of selected indices (from page)

    sel_path = get_selection_state_path()
    sel_state = read_json(sel_path)
    if account not in sel_state:
        raise HTTPException(status_code=404, detail="No pending selection")

    acct_state = sel_state[account]

    if action == "post":
        # Accept whatever selected list the page sends
        if selected is not None:
            acct_state["selected"] = selected
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

        # Trigger the pipeline
        script_path = WORKSPACE / "scripts" / "instagram_post.py"
        cfg_path = ACCOUNT_CONFIGS_DIR / f"{account}.json"
        subprocess.Popen(
            ["python3", str(script_path), "--account", account, "--config", str(cfg_path)],
            cwd=str(WORKSPACE),
        )

        return {"ok": True, "message": "Approved! Post is being published now."}

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


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[admin] Workspace: {WORKSPACE}")
    print(f"[admin] Starting Caption Pilot Admin API on http://0.0.0.0:8766")
    print(f"[admin] Admin token: {ADMIN_TOKEN}")
    uvicorn.run(app, host="0.0.0.0", port=8766, log_level="info")
