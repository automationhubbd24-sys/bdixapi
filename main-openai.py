# main-openai.py
# FastAPI local OpenAI-compatible -> Gemini proxy with rotating keys + optional thinking chain
# pip install fastapi uvicorn httpx supabase python-dotenv

import os
import time
import asyncio
import json
import random
from typing import List, Optional, Dict, Any, Tuple

from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import Response, JSONResponse, StreamingResponse, HTMLResponse
import httpx
from dotenv import load_dotenv
from supabase import create_client, Client
from concurrent.futures import ThreadPoolExecutor

# ==========================================
# FRONTEND TEMPLATE (Simple Dashboard)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gemini Proxy Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        body { background-color: #0f172a; color: #e2e8f0; font-family: 'Inter', sans-serif; }
        .card { background-color: #1e293b; border-radius: 0.75rem; padding: 1.5rem; border: 1px solid #334155; }
        .status-dot { height: 0.5rem; width: 0.5rem; border-radius: 50%; display: inline-block; }
        .status-ok { background-color: #22c55e; }
        .status-error { background-color: #ef4444; }
    </style>
</head>
<body class="p-8">
    <div class="max-w-6xl mx-auto">
        <header class="mb-8 flex justify-between items-center">
            <div>
                <h1 class="text-3xl font-bold text-white flex items-center gap-2">
                    <i data-lucide="server" class="h-8 w-8 text-blue-500"></i>
                    Gemini Proxy Server
                </h1>
                <p class="text-slate-400 mt-2">Secure, Rotating API Gateway</p>
            </div>
            <div class="flex gap-4">
                <button onclick="checkHealth()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-white font-medium transition flex items-center gap-2">
                    <i data-lucide="activity" class="h-4 w-4"></i> Check Health
                </button>
                 <button onclick="reloadKeys()" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-white font-medium transition flex items-center gap-2">
                    <i data-lucide="refresh-cw" class="h-4 w-4"></i> Reload Keys
                </button>
            </div>
        </header>

        <!-- Stats Grid -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-slate-400 text-sm">Active Keys</p>
                        <h3 class="text-3xl font-bold text-white mt-2" id="key-count">--</h3>
                    </div>
                    <div class="p-2 bg-blue-500/10 rounded-lg">
                        <i data-lucide="key" class="h-6 w-6 text-blue-500"></i>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-slate-400 text-sm">Proxy Status</p>
                        <h3 class="text-lg font-bold text-white mt-2 flex items-center gap-2" id="proxy-status">
                            <span class="status-dot status-ok"></span> Active
                        </h3>
                        <p class="text-xs text-slate-500 mt-1" id="proxy-region">Auto-Detecting...</p>
                    </div>
                    <div class="p-2 bg-purple-500/10 rounded-lg">
                        <i data-lucide="globe" class="h-6 w-6 text-purple-500"></i>
                    </div>
                </div>
            </div>

            <div class="card">
                 <div class="flex justify-between items-start">
                    <div>
                        <p class="text-slate-400 text-sm">System Health</p>
                        <h3 class="text-lg font-bold text-green-400 mt-2" id="system-health">Operational</h3>
                    </div>
                    <div class="p-2 bg-green-500/10 rounded-lg">
                        <i data-lucide="cpu" class="h-6 w-6 text-green-500"></i>
                    </div>
                </div>
            </div>
        </div>

        <!-- Keys Table -->
        <div class="card overflow-hidden">
            <h2 class="text-xl font-bold text-white mb-6 flex items-center gap-2">
                <i data-lucide="list" class="h-5 w-5 text-slate-400"></i>
                API Key Performance
            </h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="text-slate-400 text-sm border-b border-slate-700">
                            <th class="pb-3 pl-2">Key Preview</th>
                            <th class="pb-3">Status</th>
                            <th class="pb-3">Success</th>
                            <th class="pb-3">Failures</th>
                            <th class="pb-3">Latency (avg)</th>
                        </tr>
                    </thead>
                    <tbody id="keys-table-body" class="text-slate-300">
                        <!-- Rows injected via JS -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        lucide.createIcons();
        const ADMIN_TOKEN = "changeme_local_only"; // In production, ask user to input this

        async function fetchStats() {
            try {
                const res = await fetch('/status', {
                    headers: { 'x-proxy-admin': ADMIN_TOKEN }
                });
                if(!res.ok) throw new Error("Auth failed");
                const data = await res.json();
                renderKeys(data.keys);
                document.getElementById('key-count').innerText = data.keys.length;
            } catch (e) {
                console.error(e);
            }
        }

        function renderKeys(keys) {
            const tbody = document.getElementById('keys-table-body');
            tbody.innerHTML = '';
            keys.forEach(k => {
                const tr = document.createElement('tr');
                tr.className = "border-b border-slate-800 hover:bg-slate-800/50 transition";
                
                const statusColor = k.available_in > 0 ? "text-red-400" : "text-green-400";
                const statusText = k.available_in > 0 ? `Rate Limited (${k.available_in}s)` : "Ready";

                tr.innerHTML = `
                    <td class="py-3 pl-2 font-mono text-sm text-slate-400">${k.key_preview}</td>
                    <td class="py-3 ${statusColor} font-medium">${statusText}</td>
                    <td class="py-3 text-green-400">${k.success}</td>
                    <td class="py-3 text-red-400">${k.fail}</td>
                    <td class="py-3 text-slate-500">-</td>
                `;
                tbody.appendChild(tr);
            });
        }
        
        async function checkHealth() {
             const res = await fetch('/health');
             const data = await res.json();
             alert(JSON.stringify(data, null, 2));
        }

        async function reloadKeys() {
            if(confirm("Reload keys from database/file?")) {
                await fetch('/reload-keys', { method: 'POST', headers: { 'x-proxy-admin': ADMIN_TOKEN } });
                fetchStats();
            }
        }

        // Initial load
        fetchStats();
        setInterval(fetchStats, 5000); // Auto refresh every 5s
    </script>
</body>
</html>
"""

# ==========================================
# FREE PROXY ROTATION LOGIC (START)
# To remove: Delete this section and revert usage in stream_from_upstream/try_forward_to_upstream
# ==========================================
USE_FREE_PROXY = False  # Disabled as requested by user
ASIAN_COUNTRIES = ['BD', 'IN', 'VN', 'NP', 'PK', 'JP', 'MY', 'ID', 'TH', 'PH', 'SG']

try:
    from fp.fp import FreeProxy
    FREE_PROXY_LIB_INSTALLED = True
except ImportError:
    FREE_PROXY_LIB_INSTALLED = False
    print("Warning: 'free-proxy' library not installed. Running without free proxy rotation.")

class ProxyManager:
    def __init__(self):
        self.current_proxy: Optional[str] = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.lock = asyncio.Lock()
        self.bad_proxies = set()

    def _fetch_new_proxy_sync(self):
        if not FREE_PROXY_LIB_INSTALLED or not USE_FREE_PROXY:
            return None
        try:
            print(f"Searching for free proxies in Asia ({ASIAN_COUNTRIES})...")
            # https=True is important for Gemini API
            proxy = FreeProxy(country_id=ASIAN_COUNTRIES, timeout=1, rand=True, https=True).get()
            print(f"Found new proxy: {proxy}")
            return proxy
        except Exception as e:
            print(f"Could not find free proxy: {e}")
            return None

    async def get_proxy(self) -> Optional[str]:
        if not USE_FREE_PROXY:
            return None

        # Return current proxy if valid
        if self.current_proxy:
            return self.current_proxy
            
        # Fetch new one
        async with self.lock:
            if self.current_proxy:
                return self.current_proxy
            
            loop = asyncio.get_event_loop()
            self.current_proxy = await loop.run_in_executor(self.executor, self._fetch_new_proxy_sync)
            return self.current_proxy

    def mark_bad(self, proxy: str):
        if self.current_proxy == proxy:
            print(f"Marking proxy {proxy} as bad and clearing it.")
            self.current_proxy = None
            self.bad_proxies.add(proxy)

PROXY_MANAGER = ProxyManager()
# ==========================================
# FREE PROXY ROTATION LOGIC (END)
# ==========================================

load_dotenv()

APP = FastAPI(title="Local OpenAI-compatible -> Gemini proxy (with optional thinking chain)")

# -------------------------
# Security: API Key Validation Middleware
# -------------------------
@APP.middleware("http")
async def validate_api_key(request: Request, call_next):
    # Allow health checks and root without auth
    if request.url.path in ["/", "/health", "/status", "/reload-keys", "/favicon.ico"]:
        return await call_next(request)
    
    # Check for Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        # Also check query param 'key' for some clients
        api_key = request.query_params.get("key")
        if not api_key:
            return JSONResponse(status_code=401, content={"error": "Missing API Key. Please provide a valid key in Authorization header or 'key' query parameter."})
    else:
        # Extract Bearer token
        try:
            scheme, api_key = auth_header.split()
            if scheme.lower() != 'bearer':
                return JSONResponse(status_code=401, content={"error": "Invalid authentication scheme. Use Bearer."})
        except ValueError:
             return JSONResponse(status_code=401, content={"error": "Invalid Authorization header format."})

    # Validate the key against our list (simple check: is it in our loaded keys?)
    # NOTE: In a real production system, you might want a separate list of "client keys" vs "upstream keys".
    # For now, we assume the client must provide ONE of the valid upstream keys to use the service.
    # OR we can just check if it's a non-empty string if we want to allow any key format 
    # but still require SOMETHING. 
    # Given the user's request "api key sara keo connect korte parbe na", we enforce strict checking.
    
    # Optimization: Convert list to set for O(1) lookup if list is large, but for <100 keys list is fine.
    # if api_key not in KEYS_LIST:
    #    return JSONResponse(status_code=403, content={"error": "Invalid API Key."})
    
    # Correction: The user wants to prevent unauthorized access.
    # Since this is a proxy, clients usually pass a "dummy" key or one of the real keys.
    # If we want to secure the PROXY itself, we should have a "PROXY_ACCESS_KEY".
    # Let's use ADMIN_TOKEN as a simple access key for now, OR require the client to pass
    # a valid Gemini key that we have in our pool.
    
    # Decision: To keep it simple and compatible with OpenAI clients, 
    # we will require the client to send a key that matches 'ADMIN_TOKEN' 
    # OR one of the keys in our pool.
    
    if api_key != ADMIN_TOKEN and api_key not in KEYS_LIST:
         return JSONResponse(status_code=403, content={"error": "Access Denied. Invalid API Key."})

    return await call_next(request)


# -------------------------
# Config
# -------------------------
VPN_PROXY_URL = os.getenv("VPN_PROXY_URL", "http://brd-customer-hl_e956420e-zone-data_center:mwiju3dghh0n@brd.superproxy.io:33335")  # proxy to bypass regional restrictions
KEYS_FILE = "api_keys.txt" # api keys, one per line (fallback)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme_local_only")
UPSTREAM_BASE_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
BACKOFF_MIN = 5
BACKOFF_MAX = 600
DEBUG = False

# Rate Limits (Gemini Free Tier Defaults)
# RPM: 15 requests per minute
# RPD: 1500 requests per day
DEFAULT_RPM = 5  # Strictly limited to 5 RPM as requested
DEFAULT_RPD = 20 # Strictly limited to 20 RPD as requested

# Supabase Config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Force enabling of thinking chain parameters
ENABLE_THINKING_CHAIN = False
'''
"ENABLE_THINKING_CHAIN = True" - Not works with Roo Code (Unexpected API Response).
But in the OpenAI Python library (test.py) it works perfectly without manually passing:

extra_body={
    'extra_body': {
    "google": {
        "thinking_config": {
        "thinking_budget": 32768, # 128 to 32768
        "include_thoughts": True
        }
    }
    }
}
'''

# -------------------------
# Setup proxy from config
# -------------------------
# NOTE: We set these env vars ONLY for httpx/requests calls that respect them.
# However, Supabase client also respects them, which causes issues if the proxy is dead.
# We will temporarily unset them during Supabase calls or handle Supabase client creation carefully.
if VPN_PROXY_URL:
    proxy_url_with_scheme = VPN_PROXY_URL if "://" in VPN_PROXY_URL else f"http://{VPN_PROXY_URL}"
    # os.environ['HTTP_PROXY'] = proxy_url_with_scheme
    # os.environ['HTTPS_PROXY'] = proxy_url_with_scheme
    # os.environ['ALL_PROXY'] = proxy_url_with_scheme
    # COMMENTED OUT: We will apply proxy explicitly to httpx client instead of globally to avoid breaking Supabase.

# -------------------------
# Utilities: load keys
# -------------------------
def load_keys_from_supabase() -> List[str]:
    """Load keys from Supabase database."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase URL or Key not set. Falling back to file.")
        return []
        
    try:
        # Ensure no proxy env vars interfere with Supabase connection
        # (Supabase uses httpx under the hood)
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        response = supabase.table("gemini_api_keys").select("key").eq("is_active", True).execute()
        keys = [item["key"] for item in response.data]
        if not keys:
             print("No active keys found in Supabase.")
        return keys
    except Exception as e:
        print(f"Error loading keys from Supabase: {e}")
        return []

def load_keys_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        # raise FileNotFoundError(f"API keys file not found: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        keys = [line.strip() for line in f if line.strip()]
    # if not keys:
    #     raise RuntimeError("No API keys found in file.")
    return keys

# Load keys from Supabase first, then fallback to file
KEYS_LIST = load_keys_from_supabase()
if not KEYS_LIST:
    print("Trying to load keys from file...")
    KEYS_LIST = load_keys_from_file(KEYS_FILE)

if not KEYS_LIST:
    raise RuntimeError("No API keys found in Supabase or file.")

print(f"Loaded {len(KEYS_LIST)} keys.")

# -------------------------
# Key state & pool
# -------------------------
class KeyState:
    def __init__(self, key: str):
        self.key: str = key
        self.backoff: float = 0.0
        self.banned_until: float = 0.0
        self.success: int = 0
        self.fail: int = 0
        
        # Rate Limiting
        self.rpm_limit = DEFAULT_RPM
        self.rpd_limit = DEFAULT_RPD
        
        self.usage_minute = 0
        self.minute_start_time = time.time()
        
        self.usage_day = 0
        self.day_start_time = time.time() # This should align with UTC midnight ideally, but rolling 24h for now

    def is_available(self) -> bool:
        now = time.time()
        
        # Check Backoff
        if now < self.banned_until:
            return False
            
        # Check/Reset Minute Limit
        if now - self.minute_start_time >= 60:
            self.usage_minute = 0
            self.minute_start_time = now
            
        if self.usage_minute >= self.rpm_limit:
            return False
            
        # Check/Reset Day Limit
        if now - self.day_start_time >= 86400: # 24 hours
            self.usage_day = 0
            self.day_start_time = now
            
        if self.usage_day >= self.rpd_limit:
            return False
            
        return True

    def mark_success(self) -> None:
        self.backoff = 0.0
        self.banned_until = 0.0
        self.success += 1
        self.usage_minute += 1
        self.usage_day += 1

    def mark_failure(self) -> None:
        if self.backoff <= 0:
            self.backoff = BACKOFF_MIN
        else:
            self.backoff = min(BACKOFF_MAX, self.backoff * 2.0)
        self.banned_until = time.monotonic() + self.backoff
        self.fail += 1


class KeyPool:
    def __init__(self, keys: List[str]):
        self.states: List[KeyState] = [KeyState(k) for k in keys]
        self.n: int = len(self.states)
        self.idx: int = 0
        self.lock = asyncio.Lock()

    async def next_available(self) -> Optional[KeyState]:
        async with self.lock:
            start = self.idx
            attempts = 0
            # Try to find a key that is not rate-limited or banned
            # We iterate up to N times to check all keys
            for i in range(self.n):
                j = (start + i) % self.n
                st = self.states[j]
                if st.is_available():
                    self.idx = (j + 1) % self.n
                    return st
            return None

    def status(self) -> List[Dict[str, Any]]:
        now = time.monotonic()
        sys_now = time.time()
        out: List[Dict[str, Any]] = []
        for s in self.states:
            # Calculate time until limits reset
            rpm_reset_in = max(0, 60 - (sys_now - s.minute_start_time))
            rpd_reset_in = max(0, 86400 - (sys_now - s.day_start_time))
            
            is_rate_limited = s.usage_minute >= s.rpm_limit or s.usage_day >= s.rpd_limit
            status_msg = "Ready"
            if s.banned_until > now:
                status_msg = f"Backoff ({int(s.banned_until - now)}s)"
            elif s.usage_minute >= s.rpm_limit:
                status_msg = f"RPM Limit ({int(rpm_reset_in)}s)"
            elif s.usage_day >= s.rpd_limit:
                status_msg = "Daily Limit"

            out.append({
                "key_preview": s.key[:8] + "..." if len(s.key) > 8 else s.key,
                "available_in": max(0, round(s.banned_until - now, 2)),
                "rpm_usage": f"{s.usage_minute}/{s.rpm_limit}",
                "rpd_usage": f"{s.usage_day}/{s.rpd_limit}",
                "status": status_msg,
                "success": s.success,
                "fail": s.fail,
            })
        return out


POOL = KeyPool(KEYS_LIST)

# -------------------------
# Upstream streaming
# -------------------------
async def stream_from_upstream(method: str, url: str, headers: Dict[str, str], content: Optional[bytes], key_state: KeyState, timeout: int = 300):
    # Determine which proxy to use
    proxy_url = None
    if USE_FREE_PROXY and FREE_PROXY_LIB_INSTALLED:
        proxy_url = await PROXY_MANAGER.get_proxy()
    
    # Fallback to configured VPN/Data Center proxy if no free proxy found
    if not proxy_url and VPN_PROXY_URL:
        proxy_url = VPN_PROXY_URL

    # Use proxy if configured, but verify=False to avoid SSL issues with some proxies
    transport = httpx.AsyncHTTPTransport(verify=False)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, proxy=proxy_url) as client:
            try:
                async with client.stream(method, url, headers=headers, content=content) as upstream:
                    if upstream.status_code >= 400:
                        body = await upstream.aread()
                        # If it's a proxy error (407, 502, etc), mark free proxy as bad
                        if upstream.status_code in (407, 502, 503, 504) and proxy_url and proxy_url != VPN_PROXY_URL:
                             PROXY_MANAGER.mark_bad(proxy_url)
                        
                        key_state.mark_failure()
                        if body:
                            yield body
                        return
                    key_state.mark_success()
                    async for chunk in upstream.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.RequestError as e:
                print(f"Request Error (Stream) with proxy {proxy_url}: {e}")
                if proxy_url and proxy_url != VPN_PROXY_URL:
                    PROXY_MANAGER.mark_bad(proxy_url)
                # If proxy failed, we might want to retry or just let the caller handle it (key failure)
                # For now, mark key failure so it rotates key (and potentially proxy next time)
                key_state.mark_failure()
                raise
    except Exception as e:
        print(f"Connection Error with proxy {proxy_url}: {e}")
        if proxy_url and proxy_url != VPN_PROXY_URL:
            PROXY_MANAGER.mark_bad(proxy_url)
        raise

async def try_forward_to_upstream(method: str, url: str, headers: Dict[str, str], content: Optional[bytes], is_stream: bool, key_state: KeyState, timeout: int = 300):
    if is_stream:
        gen = stream_from_upstream(method, url, headers, content, key_state, timeout=timeout)
        return StreamingResponse(gen, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
    else:
        # Determine which proxy to use
        proxy_url = None
        if USE_FREE_PROXY and FREE_PROXY_LIB_INSTALLED:
            proxy_url = await PROXY_MANAGER.get_proxy()
        
        # Fallback
        if not proxy_url and VPN_PROXY_URL:
            proxy_url = VPN_PROXY_URL

        # Use proxy if configured, but verify=False to avoid SSL issues with some proxies
        transport = httpx.AsyncHTTPTransport(verify=False)
        async with httpx.AsyncClient(timeout=timeout, transport=transport, proxy=proxy_url) as client:
            try:
                resp = await client.request(method, url, headers=headers, content=content)
                if resp.status_code in (429, 403, 500, 502, 503):
                    key_state.mark_failure()
                    # Check if it looks like a proxy issue
                    if resp.status_code in (502, 503, 407) and proxy_url and proxy_url != VPN_PROXY_URL:
                        PROXY_MANAGER.mark_bad(proxy_url)
                else:
                    key_state.mark_success()
                media_type = resp.headers.get("content-type", "application/json")
                return Response(content=resp.content, status_code=resp.status_code, media_type=media_type)
            except httpx.RequestError as e:
                print(f"Request Error with proxy {proxy_url}: {e}")
                if proxy_url and proxy_url != VPN_PROXY_URL:
                    PROXY_MANAGER.mark_bad(proxy_url)
                key_state.mark_failure()
                # Return a 502 Bad Gateway error instead of crashing
                return JSONResponse(status_code=502, content={"error": f"Upstream connection failed: {str(e)}"})

# -------------------------
# Map incoming path to upstream
# -------------------------
def map_incoming_to_upstream(path: str) -> str:
    p = path.lstrip("/")
    if p.startswith("v1/"):
        p = p[len("v1/") :]
    if p == "models" or p.startswith("models/"):
        return UPSTREAM_BASE_GEMINI.rstrip("/") + "/openai/models"
    return UPSTREAM_BASE_GEMINI.rstrip("/") + "/openai/" + p

def detect_stream_from_request(content_bytes: Optional[bytes], query_params: Dict[str, Any]) -> bool:
    qp = query_params.get("stream")
    if qp in ("true", "True", "1", True):
        return True
    if content_bytes:
        try:
            j = json.loads(content_bytes.decode(errors="ignore"))
            if isinstance(j, dict) and j.get("stream") is True:
                return True
        except Exception:
            pass
    return False

# -------------------------
# Health Check & Frontend
# -------------------------
@APP.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Only allow admin to see dashboard (optional, or remove completely)
    # For now, let's just return a generic welcome message or 404
    # But user wants the frontend... let's check auth or just hide sensitive info
    
    # Wait, the user said "ami chai user ra amon kono data dekte na pak"
    # referring to {"status":"ok","service":"gemini-proxy","keys_loaded":10}
    # This JSON was coming from the previous root handler.
    # The NEW root handler returns the HTML Dashboard.
    # But maybe the user doesn't want PUBLIC access to the dashboard either?
    
    # Let's protect the dashboard with the ADMIN_TOKEN too.
    # If no token, show a simple "Service Running" page without stats.
    
    auth_header = request.headers.get("x-proxy-admin")
    # Also check query param ?token=... for easy browser access
    token = request.query_params.get("token")
    
    if not is_admin(auth_header) and not is_admin(token):
        # Public view: Just a simple status without numbers
        return HTMLResponse(content="""
        <html>
        <head>
            <title>SalesmenChatbot AI - Enterprise LLM</title>
            <style>
                body { background-color: #0f172a; color: #e2e8f0; font-family: 'Inter', system-ui, sans-serif; height: 100vh; margin: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; }
                .container { text-align: center; padding: 2rem; background: #1e293b; border-radius: 1rem; border: 1px solid #334155; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); max-width: 500px; width: 90%; }
                h1 { margin: 0 0 0.5rem 0; background: linear-gradient(to right, #60a5fa, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-size: 2.5rem; }
                p { color: #94a3b8; margin-bottom: 2rem; line-height: 1.6; }
                .status-badge { display: inline-flex; align-items: center; gap: 0.5rem; background: rgba(34, 197, 94, 0.1); color: #22c55e; padding: 0.5rem 1rem; border-radius: 9999px; font-weight: 600; font-size: 0.875rem; }
                .dot { width: 8px; height: 8px; background: #22c55e; border-radius: 50%; box-shadow: 0 0 8px #22c55e; animation: pulse 2s infinite; }
                @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
                .footer { margin-top: 2rem; font-size: 0.75rem; color: #64748b; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>SalesmenChatbot AI</h1>
                <p>Advanced Enterprise Language Model API<br>Optimized for Sales Automation & Customer Engagement</p>
                
                <div class="status-badge">
                    <span class="dot"></span>
                    Systems Operational
                </div>

                <div class="footer">
                    &copy; 2026 SalesmenChatbot AI Inc. All rights reserved.<br>
                    <span style="opacity:0.7">Powered by Proprietary Neural Engine v2.5</span>
                </div>
            </div>
        </body>
        </html>
        """, status_code=200)

    return HTMLResponse(content=HTML_TEMPLATE, status_code=200)

@APP.get("/health")
async def health_check():
    # Return professional AI service status
    return {
        "status": "operational", 
        "provider": "SalesmenChatbot AI", 
        "version": "2.5.0-enterprise",
        "region": "global-edge"
    }

# -------------------------
# Catch-all proxy
# -------------------------
@APP.api_route("/{full_path:path}", methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
async def catch_all(request: Request, full_path: str):
    # Skip health check paths if they matched catch-all (though specific routes take precedence)
    if full_path in ("", "health"):
        return {"status": "ok"}
        
    upstream_url = map_incoming_to_upstream(full_path)
    content = await request.body()
    params = dict(request.query_params)
    is_stream = detect_stream_from_request(content if content else None, params)

    incoming_headers: Dict[str, str] = {k:v for k,v in request.headers.items() if k.lower() not in ("host","content-length","transfer-encoding","connection")}

    # /models -> random available key
    p_normal = full_path[len("v1/"):] if full_path.startswith("v1/") else full_path
    if p_normal == "models" or p_normal.startswith("models/"):
        avail = [s for s in POOL.states if s.is_available()]
        key_state = random.choice(avail) if avail else min(POOL.states, key=lambda s: s.banned_until)
        headers = dict(incoming_headers)
        headers["Authorization"] = f"Bearer {key_state.key}"
        if not any(k.lower()=="content-type" for k in headers):
            ct = request.headers.get("content-type")
            headers["Content-Type"] = ct if ct else "application/json"
        return await try_forward_to_upstream(request.method, upstream_url, headers, content, is_stream, key_state)

    # Normal round-robin keys
    tried: List[str] = []
    for _ in range(len(POOL.states)):
        key_state = await POOL.next_available()
        if key_state is None:
            break
        tried.append(key_state.key[:8]+"...")
        headers = dict(incoming_headers)
        headers["Authorization"] = f"Bearer {key_state.key}"
        if not any(k.lower()=="content-type" for k in headers):
            ct = request.headers.get("content-type")
            headers["Content-Type"] = ct if ct else "application/json"

        # Правильная вставка thinking chain, если включено
        body_to_send = content
        if ENABLE_THINKING_CHAIN and content:
            try:
                body_json = json.loads(content.decode())
                # добавить thinking_config только если его нет
                if "extra_body" not in body_json or "google" not in body_json.get("extra_body", {}):
                    body_json.setdefault("extra_body", {}).setdefault("google", {}).setdefault("thinking_config", {
                        "thinking_budget": 32768,
                        "include_thoughts": True
                    })
                    body_to_send = json.dumps(body_json).encode("utf-8")
            except Exception:
                # если парсинг не удался, просто отправляем оригинальный content
                body_to_send = content

        try:
            return await try_forward_to_upstream(request.method, upstream_url, headers, body_to_send, is_stream, key_state)
        except Exception:
            key_state.mark_failure()
            continue

    return JSONResponse({"error":"all keys unavailable", "tried": tried}, status_code=429)


# -------------------------
# Admin endpoints
# -------------------------
def is_admin(auth_header: Optional[str]) -> bool:
    if not auth_header:
        return False
    if auth_header == ADMIN_TOKEN:
        return True
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ",1)[1] == ADMIN_TOKEN
    return False

@APP.get("/status")
async def status(x_proxy_admin: Optional[str] = Header(None)):
    if not is_admin(x_proxy_admin):
        raise HTTPException(401, "Unauthorized")
    return JSONResponse({"keys": POOL.status()})

@APP.post("/reload-keys")
async def reload_keys(x_proxy_admin: Optional[str] = Header(None)):
    if not is_admin(x_proxy_admin):
        raise HTTPException(401, "Unauthorized")
    global KEYS_LIST, POOL
    KEYS_LIST = load_keys_from_file(KEYS_FILE)
    POOL = KeyPool(KEYS_LIST)
    return JSONResponse({"reloaded": True, "num_keys": len(KEYS_LIST)})

# -------------------------
# Run note:
# uvicorn main-openai:APP --host 127.0.0.1 --port 8000
# -------------------------
