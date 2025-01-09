from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
