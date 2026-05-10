#!/usr/bin/env python3
"""NC-284: Probe Twilio WebSocket upgrade headers.

Empirically determines whether Twilio sends X-Twilio-Signature on the
WebSocket upgrade request for Media Streams. This answers NC-283 Amendment 1.

Usage:
    cd /path/to/yamlgraph
    source .env && python projects/voice_runtime/scripts/probe_twilio_ws_headers.py

Requirements:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, VOICE_STREAM_URL
    or: ngrok running on port 8080

Result saved to /tmp/twilio_ws_headers.json
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request

import uvicorn
from fastapi import FastAPI, WebSocket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("probe")

PORT = int(os.getenv("VOICE_SERVER_PORT", "8080"))
NGROK_API_PORT = int(os.getenv("NGROK_API_PORT", "4040"))
RESULT_FILE = "/tmp/twilio_ws_headers.json"

captured_headers: dict[str, str] = {}
capture_event = threading.Event()


def build_app() -> FastAPI:
    app = FastAPI()

    @app.websocket("/voice")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        global captured_headers
        # Capture ALL headers before accepting or rejecting
        captured_headers = dict(websocket.headers)
        logger.info("=== WebSocket upgrade headers received ===")
        for k, v in captured_headers.items():
            logger.info("  %s: %s", k, v)
        # Accept briefly then close — we only need headers
        await websocket.accept()
        await websocket.close(1000)
        capture_event.set()

    return app


def start_ngrok() -> str:
    """Start ngrok and return the public HTTPS URL."""
    # Kill existing ngrok on this port
    subprocess.run(["pkill", "-f", f"ngrok http {PORT}"], capture_output=True)
    time.sleep(1)

    authtoken = os.getenv("OUTCALLER_NGROK_AUTHTOKEN", "")
    cmd = ["ngrok", "http", str(PORT), "--log=stdout"]
    if authtoken:
        cmd += ["--authtoken", authtoken]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Poll ngrok admin API for tunnel URL
    for _ in range(30):
        time.sleep(1)
        try:
            resp = urllib.request.urlopen(
                f"http://localhost:{NGROK_API_PORT}/api/tunnels", timeout=2
            )
            data = json.loads(resp.read())
            for tunnel in data.get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            pass

    raise RuntimeError("ngrok did not start within 30s")


def place_call(stream_url: str) -> None:
    """Place a real Twilio call pointing at our probe server."""
    from voice_runtime.transports.twilio_call import initiate_outbound_call

    phone = os.getenv("TWILIO_PHONE_NUMBER", "")
    if not phone:
        raise RuntimeError("TWILIO_PHONE_NUMBER not set")

    logger.info("Placing call to %s with stream URL %s", phone, stream_url)
    # Call own number — triggers TwiML → WebSocket upgrade
    os.environ["VOICE_STREAM_URL"] = stream_url
    initiate_outbound_call(phone)


def main() -> None:
    # Determine stream URL: use existing VOICE_STREAM_URL or start ngrok
    stream_url = os.getenv("VOICE_STREAM_URL", "")
    ngrok_proc = None

    if not stream_url or "ngrok" not in stream_url and "fly.io" not in stream_url:
        logger.info("Starting ngrok on port %d...", PORT)
        stream_url = start_ngrok()
        logger.info("ngrok URL: %s", stream_url)
    else:
        logger.info("Using VOICE_STREAM_URL: %s", stream_url)

    # Start uvicorn in a background thread
    app = build_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    def run_server() -> None:
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(1)  # Let uvicorn start
    logger.info("Server started on port %d", PORT)

    # Place the call
    try:
        place_call(stream_url)
    except Exception as e:
        logger.error("Failed to place call: %s", e)
        sys.exit(1)

    # Wait for WebSocket upgrade (up to 30s)
    logger.info("Waiting for Twilio WebSocket upgrade (up to 30s)...")
    got_it = capture_event.wait(timeout=30)

    server.should_exit = True

    if not got_it:
        logger.error("TIMEOUT: No WebSocket connection received within 30s")
        sys.exit(1)

    # Analyse result
    sig = captured_headers.get("x-twilio-signature")

    print("\n" + "=" * 60)
    print("TWILIO WEBSOCKET UPGRADE HEADER PROBE RESULT")
    print("=" * 60)
    print("\nAll headers:")
    for k, v in sorted(captured_headers.items()):
        marker = " ← TARGET" if "signature" in k.lower() else ""
        print(f"  {k}: {v}{marker}")

    print()
    if sig:
        print("✅ RESULT: X-Twilio-Signature IS PRESENT")
        print(f"   Value:  {sig}")
        print("\n→ NC-283 Amendment 1: RESOLVED — signature validation is viable")
        print("→ Use this value as test fixture in NC-283 unit tests")
    else:
        print("❌ RESULT: X-Twilio-Signature IS ABSENT")
        print("\n→ NC-283 Amendment 1: BLOCKS current approach")
        print("→ NC-283 must pivot to HTTP TwiML endpoint validation instead")
    print("=" * 60)

    result = {
        "x_twilio_signature_present": sig is not None,
        "x_twilio_signature_value": sig,
        "all_headers": captured_headers,
        "stream_url": stream_url,
        "probe_date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(RESULT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved to {RESULT_FILE}")


if __name__ == "__main__":
    main()
