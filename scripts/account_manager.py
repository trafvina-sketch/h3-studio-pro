#!/usr/bin/env python3
"""Google Account Manager for Colab CLI in WSL."""

import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CONFIG_DIR = Path("/root/.config/colab-cli")
PROFILES_DIR = CONFIG_DIR / "profiles"
TOKENS_DIR = CONFIG_DIR / "tokens"
TOKEN_FILE = CONFIG_DIR / "token.json"

def get_google_userinfo(access_token):
    """Fetch userinfo from Google OAuth2 API."""
    try:
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

def refresh_google_token(token_data):
    """Try to refresh access token using refresh_token if expired."""
    refresh_token = token_data.get("refresh_token")
    client_id = token_data.get("client_id")
    client_secret = token_data.get("client_secret")
    token_uri = token_data.get("token_uri", "https://oauth2.googleapis.com/token")

    if not refresh_token or not client_id:
        return token_data

    try:
        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret or "",
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }).encode("utf-8")
        req = urllib.request.Request(token_uri, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if "access_token" in res:
                token_data["token"] = res["access_token"]
    except Exception:
        pass
    return token_data

def sync_current_profile(balance=None, rate=None):
    """Inspect current token.json, fetch user info, and save/update in profiles/."""
    if not TOKEN_FILE.exists():
        return {"status": "no_token"}

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw_text = TOKEN_FILE.read_text(encoding="utf-8")
        token_data = json.loads(raw_text)
    except Exception as e:
        return {"status": "invalid_token", "error": str(e)}

    access_token = token_data.get("token")
    userinfo = get_google_userinfo(access_token) if access_token else {}

    # If access token was expired, try refresh
    if "error" in userinfo or not userinfo.get("email"):
        import urllib.parse
        token_data = refresh_google_token(token_data)
        access_token = token_data.get("token")
        if access_token:
            userinfo = get_google_userinfo(access_token)

    email = userinfo.get("email") or token_data.get("account") or "unknown_account"
    if balance is None:
        try:
            ok_ccu, b_ccu, r_ccu, token_data = _fetch_ccu_info_direct(token_data)
            if ok_ccu and b_ccu is not None:
                balance = b_ccu
                rate = r_ccu if r_ccu is not None else 0.0
        except Exception:
            pass
    profile_path = PROFILES_DIR / f"{email}.json"

    existing_data = {}
    if profile_path.exists():
        try:
            existing_data = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    profile = {
        "email": email,
        "name": userinfo.get("name", existing_data.get("name", email.split("@")[0])),
        "picture": userinfo.get("picture", existing_data.get("picture", "")),
        "balance": balance if balance is not None else existing_data.get("balance"),
        "rate": rate if rate is not None else existing_data.get("rate"),
        "token_data": token_data
    }

    profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    ensure_token_for_profile(email)
    return profile

def ensure_token_for_profile(email):
    """Write isolated token file for a specific account email."""
    if not email:
        return None
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = PROFILES_DIR / f"{email}.json"
    if not profile_path.exists():
        return None
    try:
        prof = json.loads(profile_path.read_text(encoding="utf-8"))
        token_data = prof.get("token_data")
        if token_data:
            target_path = TOKENS_DIR / f"{email}.json"
            target_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
            return str(target_path)
    except Exception:
        pass
    return None

def sync_all_tokens():
    """Ensure all saved profiles have corresponding isolated token files."""
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    res = {}
    for f in PROFILES_DIR.glob("*.json"):
        tp = ensure_token_for_profile(f.stem)
        if tp:
            res[f.stem] = tp
    return res

def get_account_token_path(email):
    """Return isolated token file path for an account, generating it if needed."""
    if not email or email == "auto":
        return None
    p = TOKENS_DIR / f"{email}.json"
    if p.exists():
        return str(p)
    return ensure_token_for_profile(email)

def list_accounts():
    """List all saved accounts and mark the active one."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    sync_all_tokens()
    current_token_raw = TOKEN_FILE.read_text(encoding="utf-8") if TOKEN_FILE.exists() else ""
    current_refresh_token = ""
    if current_token_raw:
        try:
            current_refresh_token = json.loads(current_token_raw).get("refresh_token", "")
        except Exception:
            pass

    accounts = []
    for f in PROFILES_DIR.glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            p_ref = p.get("token_data", {}).get("refresh_token", "")
            is_active = bool(current_refresh_token and p_ref == current_refresh_token)
            tp = TOKENS_DIR / f"{f.stem}.json"
            accounts.append({
                "email": p.get("email", f.stem),
                "name": p.get("name", ""),
                "picture": p.get("picture", ""),
                "balance": p.get("balance"),
                "rate": p.get("rate"),
                "is_active": is_active,
                "token_path": str(tp) if tp.exists() else None
            })
        except Exception:
            continue

    accounts.sort(
        key=lambda a: (
            1 if float(a.get("balance") or 0.0) > 0.05 else 0,
            1 if a.get("is_active") else 0,
            float(a.get("balance") or 0.0),
        ),
        reverse=True,
    )
    return accounts

def switch_account(email):
    """Switch active account to the specified email."""
    # First save current profile if exists
    if TOKEN_FILE.exists():
        sync_current_profile()

    profile_path = PROFILES_DIR / f"{email}.json"
    if not profile_path.exists():
        return {"status": "error", "message": f"Profile {email} not found"}

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        token_data = profile.get("token_data")
        if not token_data:
            return {"status": "error", "message": "Invalid profile data"}

        TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        return {"status": "success", "email": email}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def logout():
    """Archive current token and remove active token.json."""
    if TOKEN_FILE.exists():
        sync_current_profile()
        try:
            TOKEN_FILE.unlink()
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "logged_out"}

def remove_account(email):
    """Remove a saved profile and disconnect if active."""
    profile_path = PROFILES_DIR / f"{email}.json"
    token_path = TOKENS_DIR / f"{email}.json"
    removed = False
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            p_ref = profile.get("token_data", {}).get("refresh_token", "")
            if TOKEN_FILE.exists():
                curr = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                if curr.get("refresh_token") == p_ref:
                    TOKEN_FILE.unlink()
            profile_path.unlink()
            if token_path.exists():
                token_path.unlink()
            removed = True
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "success", "removed": removed}

def _fetch_ccu_info_direct(token_data: dict) -> tuple[bool, float | None, float | None, dict]:
    """Directly query https://colab.research.google.com/tun/m/ccu-info via OAuth2 Bearer token (fast & cross-platform)."""
    token_data = refresh_google_token(dict(token_data or {}))
    access_token = token_data.get("token")
    if not access_token:
        return False, None, None, token_data
    try:
        req = urllib.request.Request(
            "https://colab.research.google.com/tun/m/ccu-info?authuser=0",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "X-Colab-Client-Agent": "colab-cli",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if raw.startswith(")]}'"):
                raw = raw.split("\n", 1)[-1].strip()
            data = json.loads(raw)
            bal = data.get("currentBalance", data.get("paidComputeUnitsBalance"))
            rate = data.get("consumptionRateHourly", 0.0)
            if bal is not None:
                return True, round(float(bal), 2), round(float(rate or 0.0), 2), token_data
    except Exception:
        pass
    return False, None, None, token_data


def _check_single_profile_usage(profile_file: Path) -> dict:
    """Query live Colab CU balance & rate for a single saved profile in parallel."""
    import re
    import subprocess
    import time

    try:
        prof = json.loads(profile_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"email": profile_file.stem, "ok": False, "error": str(e)}

    email = prof.get("email") or profile_file.stem
    token_data = prof.get("token_data") or {}

    # 1. Fast direct HTTPS query to Google Colab /tun/m/ccu-info (works on Linux VPS, WSL & Windows without CLI binary)
    ok_direct, bal_direct, rate_direct, updated_tok = _fetch_ccu_info_direct(token_data)
    if ok_direct and bal_direct is not None:
        prof["balance"] = bal_direct
        prof["rate"] = rate_direct
        prof["checked_at"] = time.strftime("%H:%M:%S")
        prof["token_data"] = updated_tok
        profile_file.write_text(json.dumps(prof, indent=2, ensure_ascii=False), encoding="utf-8")
        ensure_token_for_profile(email)
        return {"email": email, "ok": True, "balance": bal_direct, "rate": rate_direct}

    token_path = ensure_token_for_profile(email)
    if not token_path or not Path(token_path).exists():
        return {"email": email, "ok": False, "error": "missing_token"}

    env = dict(os.environ)
    env["COLAB_TOKEN_PATH"] = str(token_path)
    local_bin = "/root/.local/bin"
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

    colab_bin = "/root/.local/bin/colab"
    if not os.path.exists(colab_bin):
        colab_bin = "colab"

    try:
        r = subprocess.run(
            [colab_bin, "--auth=oauth2", "usage"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        )
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        if r.returncode == 0:
            m_bal = re.search(r"balance:\s*([\d,]+(?:\.\d+)?)", out, re.I)
            m_rate = re.search(r"rate:\s*([\d,]+(?:\.\d+)?)", out, re.I)
            bal = float(m_bal.group(1).replace(",", "")) if m_bal else prof.get("balance")
            rate = float(m_rate.group(1).replace(",", "")) if m_rate else prof.get("rate")
            prof["balance"] = bal
            prof["rate"] = rate
            prof["checked_at"] = time.strftime("%H:%M:%S")
            # Sync updated access token if colab-cli refreshed it
            try:
                updated_tok = json.loads(Path(token_path).read_text(encoding="utf-8"))
                if updated_tok.get("token"):
                    prof["token_data"] = updated_tok
            except Exception:
                pass
            profile_file.write_text(json.dumps(prof, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"email": email, "ok": True, "balance": bal, "rate": rate}
        return {"email": email, "ok": False, "balance": prof.get("balance"), "rate": prof.get("rate"), "error": out.strip()[:200]}
    except Exception as e:
        return {"email": email, "ok": False, "balance": prof.get("balance"), "rate": prof.get("rate"), "error": str(e)}


def refresh_all_balances(auto_switch_best: bool = True) -> dict:
    """Refresh CU balance & usage rate of ALL saved accounts in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        try:
            sync_current_profile()
        except Exception:
            pass

    profile_files = sorted(PROFILES_DIR.glob("*.json"))
    results = []
    if profile_files:
        max_w = min(8, max(1, len(profile_files)))
        with ThreadPoolExecutor(max_workers=max_w) as ex:
            results = list(ex.map(_check_single_profile_usage, profile_files))

    accounts = list_accounts()

    # Auto-switch primary account if current active account has 0 CU while another account has > 0.05 CU
    active_acc = next((a for a in accounts if a.get("is_active")), None)
    funded_accs = [a for a in accounts if (a.get("balance") or 0) > 0.05]
    funded_accs.sort(key=lambda x: float(x.get("balance") or 0.0), reverse=True)

    if auto_switch_best and funded_accs:
        if not active_acc or float(active_acc.get("balance") or 0.0) <= 0.05:
            best_email = funded_accs[0]["email"]
            switch_account(best_email)
            accounts = list_accounts()
            active_acc = next((a for a in accounts if a.get("is_active")), funded_accs[0])

    if not active_acc and accounts:
        active_acc = accounts[0]

    total_cu = round(sum(float(a.get("balance") or 0.0) for a in accounts if (a.get("balance") or 0) > 0), 2)
    return {
        "status": "connected" if accounts else "not_connected",
        "email": active_acc.get("email") if active_acc else None,
        "name": active_acc.get("name") if active_acc else None,
        "picture": active_acc.get("picture") if active_acc else None,
        "balance": active_acc.get("balance") if active_acc else None,
        "rate": active_acc.get("rate") if active_acc else None,
        "total_cu": total_cu,
        "funded_count": len(funded_accs),
        "total_accounts": len(accounts),
        "accounts": accounts,
        "results": results,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "sync":
        bal = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != "None" else None
        rate = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "None" else None
        res = sync_current_profile(balance=bal, rate=rate)
        print(json.dumps(res))
    elif cmd == "refresh_all":
        print(json.dumps(refresh_all_balances()))
    elif cmd == "sync_tokens":
        print(json.dumps(sync_all_tokens()))
    elif cmd == "get_token":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps({"token_path": get_account_token_path(target)}))
    elif cmd == "list":
        print(json.dumps(list_accounts()))
    elif cmd == "switch":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(switch_account(target)))
    elif cmd == "logout":
        print(json.dumps(logout()))
    elif cmd == "remove":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(remove_account(target)))
    else:
        print(json.dumps({"error": f"Unknown command {cmd}"}))
