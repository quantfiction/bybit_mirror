from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import sys
import time
import hmac
import hashlib
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("bybit_mirror")

BYBIT_API_URL = "https://api.bybit.com"
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
RECV_WINDOW = os.getenv("BYBIT_RECV_WINDOW", "5000")

# Optional shared secret. If set, callers must send it in X-Proxy-Secret.
# This is defense-in-depth: even bound to localhost, any local process can
# otherwise issue signed trades with your real keys.
PROXY_SECRET = os.getenv("PROXY_SECRET")

# Comma-separated list of allowed CORS origins. Empty => CORS middleware is
# not installed at all (correct default for non-browser clients).
CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()
]

if not API_KEY or not API_SECRET:
    raise ValueError("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pooled client for the process instead of one per request.
    app.state.client = httpx.AsyncClient(
        base_url=BYBIT_API_URL,
        timeout=httpx.Timeout(10.0),
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)

if CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


def generate_signature(secret: str, payload: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/v5/{path:path}")
@app.get("/v5/{path:path}")
async def proxy_request(path: str, request: Request):
    if PROXY_SECRET and request.headers.get("X-Proxy-Secret") != PROXY_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.body()
    # Raw query string exactly as received; Bybit signs these bytes verbatim,
    # so we must not re-encode via QueryParams.
    query_string = request.url.query

    timestamp = str(int(time.time() * 1000))

    # Bybit v5 pre-sign string:
    #   GET:  timestamp + api_key + recv_window + queryString
    #   POST: timestamp + api_key + recv_window + rawRequestBody
    # Sign one OR the other based on method, never both.
    if request.method == "GET":
        payload = query_string
    else:
        payload = body.decode("utf-8")

    sign_str = f"{timestamp}{API_KEY}{RECV_WINDOW}{payload}"
    signature = generate_signature(API_SECRET, sign_str)

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": signature,
    }
    if request.method == "POST":
        headers["Content-Type"] = "application/json"

    # Build the URL with the raw query string so the bytes sent match the bytes
    # signed. Do NOT pass params= (httpx would re-encode/reorder them).
    url = f"/v5/{path}"
    if query_string:
        url = f"{url}?{query_string}"

    client: httpx.AsyncClient = request.app.state.client
    try:
        upstream = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if request.method == "POST" else None,
        )
    except httpx.RequestError as e:
        logger.warning("Upstream request failed: %s %s -> %s", request.method, path, e)
        # 502: we (the gateway) could not reach the upstream.
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")

    logger.info("%s /v5/%s -> %s", request.method, path, upstream.status_code)

    # Pass Bybit's status, body, and useful headers through untouched, so real
    # retCode/retMsg and rate-limit headers survive.
    passthrough = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() == "content-type" or k.lower().startswith("x-bapi")
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
    )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    # Auto-restart on code changes is a dev-only convenience. Gate it behind DEV
    # so it never runs in a real deployment.
    if os.getenv("DEV", "").lower() in ("1", "true", "yes"):
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class RestartHandler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.src_path.endswith(".py"):
                    print("Detected code change, restarting...")
                    os.execv(sys.executable, ["python"] + sys.argv)

        observer = Observer()
        observer.schedule(RestartHandler(), path=".", recursive=True)
        observer.start()
        try:
            uvicorn.run(app, host=host, port=port)
        finally:
            observer.stop()
            observer.join()
    else:
        uvicorn.run(app, host=host, port=port)
