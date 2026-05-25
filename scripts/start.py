"""
Container entrypoint.
1. Waits for ngrok tunnel to be ready
2. Pulls the public HTTPS URL from ngrok local API
3. Updates Telnyx Call Control App webhook URL automatically
4. Starts uvicorn
"""
import os
import sys
import time
import httpx
import subprocess

NGROK_API = "http://ngrok:4040/api/tunnels"
TELNYX_API = "https://api.telnyx.com/v2/call_control_applications"


def wait_for_ngrok(retries: int = 30, delay: float = 2.0) -> str:
    print("Waiting for ngrok tunnel...")
    for i in range(retries):
        try:
            r = httpx.get(NGROK_API, timeout=3)
            tunnels = r.json().get("tunnels", [])
            https = [t for t in tunnels if t["proto"] == "https"]
            if https:
                url = https[0]["public_url"]
                print(f"ngrok tunnel: {url}")
                return url
        except Exception:
            pass
        time.sleep(delay)
        print(f"  ... waiting ({i+1}/{retries})")
    print("WARN: ngrok not ready — using PUBLIC_URL from env")
    return os.environ.get("PUBLIC_URL", "")


def update_telnyx_webhook(public_url: str) -> None:
    api_key = os.environ.get("TELNYX_API_KEY", "")
    app_id  = os.environ.get("TELNYX_APP_ID", "")
    if not api_key or not app_id:
        print("WARN: TELNYX_API_KEY or TELNYX_APP_ID not set — skipping webhook update")
        return

    webhook_url = f"{public_url}/webhook/telnyx"
    try:
        r = httpx.patch(
            f"{TELNYX_API}/{app_id}",
            json={"webhook_event_url": webhook_url,
                  "webhook_event_failover_url": ""},
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code in (200, 201):
            print(f"Telnyx webhook updated -> {webhook_url}")
        else:
            print(f"WARN: Telnyx webhook update returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"WARN: Telnyx webhook update failed: {e}")


if __name__ == "__main__":
    public_url = wait_for_ngrok()

    if public_url:
        os.environ["PUBLIC_URL"] = public_url
        update_telnyx_webhook(public_url)
    else:
        print("WARN: No ngrok URL — webhook not registered")

    print("Starting uvicorn...")
    os.execvp("uvicorn", [
        "uvicorn", "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8090",
        "--log-level", os.environ.get("LOG_LEVEL", "info").lower(),
    ])
