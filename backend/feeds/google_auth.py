"""Google OAuth for the email feed: read-only, and checked to be read-only.

The only scope ever requested is ``gmail.readonly``. Both at sign-in and on every token
refresh the scope Google reports is compared with that; anything else is refused, so the
stored credential cannot send, delete, label or otherwise change the mailbox.

Tokens live in ``backend/state/`` (never in the vault or in git) with owner-only access.
Nothing here prints a token, a client secret or an authorisation code.
"""

import json
import os
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from backend import config

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
LOOPBACK_PORT = 8765


class AuthError(RuntimeError):
    """Sign-in is missing, expired, revoked or wider than read-only."""


def _client() -> dict:
    try:
        with open(config.GOOGLE_CLIENT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        info = data.get("installed") or data["web"]
        return {"client_id": info["client_id"], "client_secret": info["client_secret"]}
    except (OSError, KeyError, json.JSONDecodeError):
        raise AuthError("the Google client file is missing or not a Desktop-app OAuth client") from None


def check_scope(granted: str | None) -> None:
    scopes = set((granted or "").split())
    if scopes != {READONLY_SCOPE}:
        raise AuthError("Google granted something other than read-only Gmail access; credential refused")


def _save_token(refresh_token: str) -> None:
    os.makedirs(os.path.dirname(config.GOOGLE_TOKEN_FILE), exist_ok=True)
    fd = os.open(config.GOOGLE_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"refresh_token": refresh_token, "scope": READONLY_SCOPE}, f)
    os.chmod(config.GOOGLE_TOKEN_FILE, 0o600)


def is_authorised() -> bool:
    return os.path.isfile(config.GOOGLE_TOKEN_FILE)


async def access_token(http_client) -> str:
    """A short-lived access token, after confirming it is read-only."""
    try:
        with open(config.GOOGLE_TOKEN_FILE, encoding="utf-8") as f:
            refresh_token = json.load(f)["refresh_token"]
    except (OSError, KeyError, json.JSONDecodeError):
        raise AuthError("not signed in: run  python3 -m backend.feeds.mail authorize") from None
    res = await http_client.post(TOKEN_URL, data={**_client(), "refresh_token": refresh_token,
                                                  "grant_type": "refresh_token"})
    if res.status_code != 200:
        raise AuthError(f"Google refused the saved sign-in (HTTP {res.status_code}); authorise again")
    data = res.json()
    check_scope(data.get("scope"))
    return data["access_token"]


def authorise_interactively() -> None:
    """One-off sign-in using the loopback flow. Prints a URL to open in a browser."""
    import httpx

    client = _client()
    redirect = f"http://127.0.0.1:{LOOPBACK_PORT}"
    expected_state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": READONLY_SCOPE, "access_type": "offline", "prompt": "consent", "state": expected_state})
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = query.get("state", [""])[0] == expected_state and "code" in query
            if ok:
                result["code"] = query["code"][0]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Signed in. You can close this tab." if ok else "Sign-in failed.").encode())

        def log_message(self, *args):       # the request line contains the code: never log it
            pass

    print("Open this address in a browser on a machine that can reach this one on port "
          f"{LOOPBACK_PORT}\n(from a laptop:  ssh -L {LOOPBACK_PORT}:127.0.0.1:{LOOPBACK_PORT} <this server>  first):\n")
    print(url + "\n")
    print("You will see a warning that Google hasn't verified the app: choose Advanced, then continue.\n"
          "The only permission asked for should be to VIEW your email. Waiting...")
    server = HTTPServer(("127.0.0.1", LOOPBACK_PORT), Handler)
    while "code" not in result:
        server.handle_request()
    server.server_close()

    res = httpx.post(TOKEN_URL, timeout=30, data={**client, "code": result["code"],
                                                 "grant_type": "authorization_code", "redirect_uri": redirect})
    if res.status_code != 200:
        raise AuthError(f"Google refused the sign-in (HTTP {res.status_code})")
    data = res.json()
    check_scope(data.get("scope"))
    if not data.get("refresh_token"):
        raise AuthError("Google returned no long-lived token; remove the app's access in your Google "
                        "account and authorise again")
    _save_token(data["refresh_token"])
    print("Signed in with read-only access. The credential cannot send or change mail.")
