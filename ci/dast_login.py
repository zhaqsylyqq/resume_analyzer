"""SEC-04: sign in to the throw-away copy and print the session cookie for the ZAP scan (CI only).

    python ci/dast_login.py http://127.0.0.1:8000   # prints "session=…"; DAST_USER / DAST_PASSWORD

The scanner sends this cookie with every request, so it sees the pages of a signed-in recruiter.
"""
from __future__ import annotations

import os
import re
import sys

import httpx


def session_cookie(response: httpx.Response) -> str | None:
    # The cookie is Secure (production) and the copy is served over http, so the client would
    # not send it back by itself: it is taken from each answer and passed on by hand.
    found = re.search(r"\b(session=[^;]+)", response.headers.get("set-cookie", ""))
    return found.group(1) if found else None


def main(base: str) -> int:
    with httpx.Client(base_url=base, follow_redirects=False, timeout=10) as client:
        page = client.get("/login")
        token = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
        if token is None or session_cookie(page) is None:
            print("no CSRF token or session on /login", file=sys.stderr)
            return 1
        response = client.post("/login", headers={"Cookie": session_cookie(page) or ""},
                               data={"csrf_token": token.group(1), "username": os.environ["DAST_USER"],
                                     "password": os.environ["DAST_PASSWORD"]})
        cookie = session_cookie(response)
        if response.status_code != 302 or cookie is None:
            print(f"sign-in failed: HTTP {response.status_code}", file=sys.stderr)
            return 1
        check = client.get("/", headers={"Cookie": cookie})
        if check.status_code != 200:
            print(f"the cookie does not open the start page: HTTP {check.status_code}", file=sys.stderr)
            return 1
    print(cookie)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
