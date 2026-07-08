#!/usr/bin/env python3
"""Windows-side CDP client that captures the Cognito /oauth2/token bundle.

This runs under **Windows** ``python.exe`` (NOT WSL python): WSL is NAT-networked and
cannot reach the Windows loopback where Edge exposes its DevTools port, so the CDP
client must live on the Windows side where ``127.0.0.1`` resolves to Edge. It is a
standalone script — it must not import the ``parkbot`` package.

It is spawned by ``parkbot.bootstrap_edge`` after native Edge is launched with a
remote-debugging port. It attaches to every page target, enables the Network domain,
and watches for the ``/oauth2/token`` response on the Cognito hosted-UI domain. When it
sees a body that contains a ``refresh_token`` (the authorization_code exchange), it
writes that JSON bundle to ``--out`` (a ``C:\\`` path) and exits 0.

Exit codes: 0 = captured, 2 = timeout / nothing captured, 3 = Edge debug port unreachable.

Usage (invoked from WSL, args are Windows paths):
    python.exe win_cdp_capture.py --port 9333 --cognito-domain <d> \
        --out "C:\\...\\parkbot_token_capture.json" --timeout 300
"""
import argparse
import base64
import json
import sys
import time
import urllib.request

from websocket import create_connection, WebSocketTimeoutException


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _dismiss_distractions(port: int) -> None:
    """Close Edge's fresh-profile sync/sign-in dialog + welcome tabs and bring the
    portal tab to the front. On a clean profile these steal focus and block the user
    from ever completing the SSO login."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            targets = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return
    for t in targets:
        if t.get("type") != "page":
            continue
        u, tid = t.get("url", ""), t.get("id")
        if not tid:
            continue
        distraction = ("sync-confirmation" in u or u.startswith("edge://welcome")
                       or u.startswith("edge://signin") or "first-run" in u)
        try:
            if distraction:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/close/{tid}", timeout=3).read()
            elif "parcheggimilanofiorinord.it" in u:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/activate/{tid}", timeout=3).read()
        except Exception:  # noqa: BLE001
            pass


def _browser_ws(port: int, deadline: float):
    """Poll /json/version until Edge's DevTools endpoint answers; return its ws URL."""
    url = f"http://127.0.0.1:{port}/json/version"
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                data = json.loads(r.read().decode())
            return data["webSocketDebuggerUrl"], data.get("Browser", "?")
        except Exception as e:  # noqa: BLE001 - Edge may still be starting
            last = e
            time.sleep(1.0)
    raise RuntimeError(f"Edge debug port {port} never came up: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--cognito-domain", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    deadline = time.time() + a.timeout
    # Give Edge up to 45s to bring the debug port up, but never past the overall deadline.
    try:
        ws_url, browser = _browser_ws(a.port, min(deadline, time.time() + 45))
    except Exception as e:  # noqa: BLE001
        log(f"  ! {e}")
        return 3
    log(f"  · connected to {browser} (CDP :{a.port})")

    ws = create_connection(
        ws_url,
        origin=f"http://127.0.0.1:{a.port}",  # must satisfy --remote-allow-origins
        enable_multithread=True,
    )
    ws.settimeout(1.0)

    _id = [0]

    def send(method: str, params: dict | None = None, session: str | None = None) -> int:
        _id[0] += 1
        m = {"id": _id[0], "method": method, "params": params or {}}
        if session:
            m["sessionId"] = session
        ws.send(json.dumps(m))
        return _id[0]

    # Attach to all page targets (present + future) via flattened sessions.
    send("Target.setAutoAttach",
         {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})

    watched: dict[str, str] = {}   # requestId -> sessionId (a token response we saw)
    body_cmd: dict[int, str] = {}  # getResponseBody command id -> requestId
    enabled: set[str] = set()      # sessionIds that have Network enabled
    captured: dict | None = None

    _dismiss_distractions(a.port)  # clear the fresh-profile sync dialog up front
    next_cleanup = time.time() + 4.0

    while time.time() < deadline and captured is None:
        if time.time() >= next_cleanup:
            _dismiss_distractions(a.port)
            next_cleanup = time.time() + 4.0
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            continue
        except Exception as e:  # noqa: BLE001
            log(f"  ! websocket recv error: {e}")
            break
        if not raw:
            continue
        msg = json.loads(raw)
        method = msg.get("method")
        sess = msg.get("sessionId")

        if method == "Target.attachedToTarget":
            s = msg["params"]["sessionId"]
            if s not in enabled:
                enabled.add(s)
                send("Network.enable",
                     {"maxTotalBufferSize": 100_000_000,
                      "maxResourceBufferSize": 50_000_000}, session=s)
                # Cascade auto-attach so OOPIFs / workers under this target are covered.
                send("Target.setAutoAttach",
                     {"autoAttach": True, "waitForDebuggerOnStart": False,
                      "flatten": True}, session=s)

        elif method == "Network.responseReceived":
            url = msg["params"]["response"]["url"]
            if "/oauth2/token" in url and a.cognito_domain in url:
                watched[msg["params"]["requestId"]] = sess

        elif method == "Network.loadingFinished":
            rid = msg["params"]["requestId"]
            if rid in watched:
                cid = send("Network.getResponseBody", {"requestId": rid},
                           session=watched[rid])
                body_cmd[cid] = rid

        elif "id" in msg and msg["id"] in body_cmd:
            body_cmd.pop(msg["id"])
            res = msg.get("result") or {}
            body = res.get("body", "")
            if res.get("base64Encoded") and body:
                try:
                    body = base64.b64decode(body).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    body = ""
            try:
                data = json.loads(body)
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, dict) and "refresh_token" in data:
                captured = data
                log(f"  ✓ captured token bundle "
                    f"(expires_in={data.get('expires_in')}s)")

    try:
        ws.close()
    except Exception:  # noqa: BLE001
        pass

    if captured is None:
        log("  ! no /oauth2/token bundle captured before timeout.")
        return 2

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(captured, f)
    log(f"  ✓ wrote token bundle to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
