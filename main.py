from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import httpx
import os
import sys
import time
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Allow CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BYBIT_API_URL = "https://api.bybit.com"
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("Please set BYBIT_API_KEY and BYBIT_API_SECRET in .env file")

def generate_signature(secret: str, params: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        params.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

@app.post("/v5/{path:path}")
@app.get("/v5/{path:path}")
async def proxy_request(path: str, request: Request):
    try:
        # Forward the request to Bybit
        async with httpx.AsyncClient() as client:
            # Get request body and params first
            body = await request.body()
            params = request.query_params
            
            timestamp = str(int(time.time() * 1000))
            recv_window = "5000"
            
            # Generate signature
            params_str = f"{timestamp}{API_KEY}{recv_window}"
            if params:
                params_str += str(params)
            if body:
                params_str += body.decode("utf-8")
            signature = generate_signature(API_SECRET, params_str)
            
            headers = {
                "X-BAPI-API-KEY": API_KEY,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
                "Content-Type": "application/json"
            }
            
            response = await client.request(
                method=request.method,
                url=f"{BYBIT_API_URL}/v5/{path}",
                headers=headers,
                params=params,
                content=body
            )
            
            return response.json()
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
class RestartHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith('.py'):
            print("Detected code change, restarting...")
            os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == "__main__":
    import uvicorn
    event_handler = RestartHandler()
    observer = Observer()
    observer.schedule(event_handler, path='.', recursive=True)
    observer.start()
    
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        observer.stop()
        observer.join()
