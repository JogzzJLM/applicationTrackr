from __future__ import annotations

import html
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import msal
import requests

from core.storage import DATA_DIR

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read"]
TOKEN_CACHE_FILE = Path(DATA_DIR) / "microsoft_msal_cache.json"


def _clean_html(value: str) -> str:
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", value or "", flags=re.I | re.S)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    try:
        if TOKEN_CACHE_FILE.exists():
            cache.deserialize(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    try:
        TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")
        try:
            os.chmod(TOKEN_CACHE_FILE, 0o600)
        except Exception:
            pass
    except Exception as exc:
        print(f"  ├── ⚠️ Could not persist Microsoft token cache: {exc}")


def _build_app(client_id: str, tenant: str, cache: msal.SerializableTokenCache):
    authority = f"https://login.microsoftonline.com/{tenant or 'common'}"
    return msal.PublicClientApplication(client_id=client_id, authority=authority, token_cache=cache)


def acquire_access_token(client_id: str, tenant: str = "common", login_hint: str = "") -> Dict[str, Any]:
    """Acquire a Graph token, silently when possible, otherwise by one-time device login."""
    if not client_id:
        return {"error": "missing_client_id", "error_description": "MICROSOFT_CLIENT_ID is not configured"}

    cache = _load_cache()
    app = _build_app(client_id, tenant, cache)
    accounts = app.get_accounts(username=login_hint) if login_hint else app.get_accounts()
    result: Optional[Dict[str, Any]] = None

    for account in accounts:
        result = app.acquire_token_silent(SCOPES, account=account)
        if result and result.get("access_token"):
            _save_cache(cache)
            result["auth_mode"] = "silent"
            return result

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        _save_cache(cache)
        return flow

    print("  ├── 🔐 Outlook needs one-time Microsoft sign-in.")
    print(f"  │   Open: {flow.get('verification_uri') or flow.get('verification_uri_complete')}")
    print(f"  │   Code: {flow.get('user_code')}")
    print("  │   After approval, ApplicationTrackr will cache a refresh token and reuse it automatically.")

    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if result and result.get("access_token"):
        result["auth_mode"] = "device_code"
    return result or {"error": "authentication_failed"}


def fetch_recent_inbox_messages(
    client_id: str,
    tenant: str = "common",
    login_hint: str = "",
    days: int = 3,
    limit: int = 50,
) -> Dict[str, Any]:
    token = acquire_access_token(client_id, tenant=tenant, login_hint=login_hint)
    access_token = token.get("access_token")
    if not access_token:
        return {"ok": False, "auth": token, "messages": []}

    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params = {
        "$top": str(max(1, min(limit, 100))),
        "$orderby": "receivedDateTime desc",
        "$filter": f"receivedDateTime ge {since}",
        "$select": "id,subject,from,body,receivedDateTime,isRead,internetMessageId",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        'Prefer': 'outlook.body-content-type="text"',
    }

    try:
        response = requests.get(f"{GRAPH_BASE}/me/mailFolders/inbox/messages", params=params, headers=headers, timeout=20)
    except Exception as exc:
        return {"ok": False, "auth": token, "messages": [], "error": f"Graph request failed: {exc}"}

    if response.status_code == 401:
        # A revoked/invalid refresh path should result in a fresh interactive login next cycle.
        try:
            TOKEN_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    if response.status_code != 200:
        return {
            "ok": False,
            "auth": token,
            "messages": [],
            "error": f"Microsoft Graph HTTP {response.status_code}: {response.text[:300]}",
        }

    payload = response.json() if response.content else {}
    messages: List[Dict[str, Any]] = []
    for item in payload.get("value", []) if isinstance(payload, dict) else []:
        sender = (((item.get("from") or {}).get("emailAddress") or {}).get("address") or "")
        body = item.get("body") or {}
        content = body.get("content", "")
        if str(body.get("contentType", "")).lower() == "html":
            content = _clean_html(content)
        messages.append({
            "id": item.get("id", ""),
            "internet_message_id": item.get("internetMessageId", ""),
            "subject": item.get("subject", "") or "",
            "from": sender,
            "body": content or "",
            "received_at": item.get("receivedDateTime", "") or "",
            "is_read": bool(item.get("isRead")),
        })

    return {
        "ok": True,
        "auth_mode": token.get("auth_mode", "silent"),
        "messages": messages,
    }
