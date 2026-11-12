"""Who is looking at the site: Aiia (the owner) or a viewer (for example her partner).

The site has no passwords. `tailscale serve` tells it which Tailscale account opened the page (the header
Tailscale-User-Login). OWNER_LOGIN in .env lists the owner's account(s), separated by commas:

- OWNER_LOGIN not set: nothing is restricted (everyone is the owner).
- the account is in the list: owner.
- another account: viewer, who sees only the shared jars.
- no account at all: owner only when the request has not come through the proxy (opened on the server itself);
  a request that came through the proxy without an account is treated as a viewer.
"""

import os

PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-forwarded-proto")


def owners() -> set[str]:
    return {x.strip().casefold() for x in os.environ.get("OWNER_LOGIN", "").split(",") if x.strip()}


def login_of(request) -> str:
    return request.headers.get("tailscale-user-login", "").strip()


def proxied(request) -> bool:
    return any(h in request.headers for h in PROXY_HEADERS)


def role(request) -> str:
    allowed = owners()
    if not allowed:
        return "owner"
    login = login_of(request)
    if login:
        return "owner" if login.casefold() in allowed else "viewer"
    return "viewer" if proxied(request) else "owner"


# What a viewer may open: the shared jars (read only), the page that says who they are, and the app files.
VIEWER_PATHS = {"/jars", "/whoami", "/manifest.webmanifest", "/sw.js"}


def viewer_may_open(request) -> bool:
    path = request.url.path
    return request.method == "GET" and (path in VIEWER_PATHS or path.startswith("/static/"))
