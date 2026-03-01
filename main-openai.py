# main-openai.py
# FastAPI local OpenAI-compatible -> Gemini proxy with rotating keys + optional thinking chain
# pip install fastapi uvicorn httpx supabase python-dotenv

from fastapi import FastAPI, Request, Response, HTTPException, Header, Query, Form, Depends
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple
import os
import time
import asyncio
import json
import httpx
import random
import string
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timezone
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
    # Allow health checks, root (login), admin routes, and favicon without auth
    # Also allow /v1 root to show status message (but subpaths need auth)
    allowed_paths = ["/", "/health", "/status", "/reload-keys", "/favicon.ico", "/admin", "/admin/login", "/admin/logout", "/v1"]
    
    # Allow GET /v1/chat/completions without auth (just for status message)
    if request.method == "GET" and request.url.path.endswith("chat/completions"):
        return await call_next(request)
    
    if request.url.path in allowed_paths:
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
    
    is_valid_key = False
    if api_key == ADMIN_TOKEN:
        is_valid_key = True
    elif 'POOL' in globals():
        for s in POOL.states:
            if s.key == api_key:
                is_valid_key = True
                break
    
    if not is_valid_key:
         return JSONResponse(status_code=403, content={"error": "Access Denied. Invalid API Key."})

    return await call_next(request)


# -------------------------
# Config
# -------------------------
VPN_PROXY_URL = os.getenv("VPN_PROXY_URL", "http://brd-customer-hl_e956420e-zone-data_center:mwiju3dghh0n@brd.superproxy.io:33335")  # proxy to bypass regional restrictions

def get_rotating_proxy_url():
    """
    Injects a random session ID into the Bright Data proxy URL to ensure IP rotation per request.
    Format: user-session-RANDOM:pass@host:port
    """
    if not VPN_PROXY_URL or "brd.superproxy.io" not in VPN_PROXY_URL:
        return VPN_PROXY_URL
    
    try:
        # Parse the URL
        # Expected format: http://USER:PASS@HOST:PORT
        # We need to insert -session-RANDOM into the USER part
        from urllib.parse import urlparse, urlunparse
        
        parsed = urlparse(VPN_PROXY_URL)
        if not parsed.username:
            return VPN_PROXY_URL
            
        import random
        import string
        session_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        
        # Check if session is already present (to avoid double adding)
        if "-session-" in parsed.username:
            # Replace existing session? Or just append? Bright data usually takes last one or specific format.
            # Safer to assume user provided clean base credentials without session, or we append if not present.
            # If present, let's assume user configured it static intentionaly, OR we replace it.
            # Let's replace it for rotation.
            base_user = parsed.username.split("-session-")[0]
            new_user = f"{base_user}-session-{session_id}"
        else:
            new_user = f"{parsed.username}-session-{session_id}"
            
        # Reconstruct URL
        # urlunparse needs (scheme, netloc, path, params, query, fragment)
        # netloc includes user:pass@host:port
        new_netloc = f"{new_user}:{parsed.password}@{parsed.hostname}:{parsed.port}"
        
        return urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        
    except Exception as e:
        print(f"Error rotating proxy session: {e}")
        return VPN_PROXY_URL

KEYS_FILE = "api_keys.txt" # api keys, one per line (fallback)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme_local_only")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
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
def load_keys_from_supabase() -> List[Dict[str, Any]]:
    """Load keys and their usage stats from Supabase database."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase URL or Key not set. Falling back to file.")
        return []
        
    try:
        # Ensure no proxy env vars interfere with Supabase connection
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        # Select key and usage stats
        # Assuming table has columns: key, usage_count_day, last_used_at
        # If columns don't exist, we'll handle it gracefully
        try:
            response = supabase.table("gemini_api_keys").select("key, usage_count_day, last_used_at").eq("is_active", True).execute()
        except Exception as e:
            # Fallback if columns don't exist: just select keys
            print(f"Warning: Usage columns missing in Supabase ({e}). Loading keys only.")
            response = supabase.table("gemini_api_keys").select("key").eq("is_active", True).execute()
        
        keys_data = []
        for item in response.data:
            keys_data.append({
                "key": item["key"],
                "usage_day": item.get("usage_count_day", 0) or 0,
                "last_used": item.get("last_used_at", "")
            })
            
        if not keys_data:
             print("No active keys found in Supabase.")
        return keys_data
    except Exception as e:
        print(f"Error loading keys from Supabase: {e}")
        return []

def load_keys_from_file(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        # File only has keys, so init usage to 0
        return [{"key": line.strip(), "usage_day": 0} for line in f if line.strip()]

# Load keys
KEYS_DATA = load_keys_from_supabase()
if not KEYS_DATA:
    print("Trying to load keys from file...")
    KEYS_DATA = load_keys_from_file(KEYS_FILE)

if not KEYS_DATA:
    raise RuntimeError("No API keys found in Supabase or file.")

print(f"Loaded {len(KEYS_DATA)} keys.")

from datetime import datetime, timezone

# ... rest of imports ...

# -------------------------
# Key state & pool
# -------------------------
class KeyState:
    def __init__(self, key_data: Dict[str, Any]):
        self.key: str = key_data["key"]
        self.backoff: float = 0.0
        self.banned_until: float = 0.0
        self.success: int = 0
        self.fail: int = 0
        
        # Rate Limiting
        self.rpm_limit = DEFAULT_RPM
        self.rpd_limit = DEFAULT_RPD
        
        self.usage_minute = 0
        self.minute_start_time = time.time()
        
        # Initialize usage from DB
        db_usage = key_data.get("usage_day", 0)
        last_used_str = key_data.get("last_used", "")
        
        # Reset day count if last used was NOT today (UTC)
        today_date = datetime.now(timezone.utc).date()
        try:
            if last_used_str:
                # Format expected: '2026-02-28 07:43:28' (from our update logic)
                last_used_date = datetime.strptime(last_used_str.split()[0], '%Y-%m-%d').date()
                if last_used_date < today_date:
                    print(f"INFO: Resetting key {self.key[:8]} for new day.")
                    self.usage_day = 0
                else:
                    self.usage_day = db_usage
            else:
                self.usage_day = db_usage
        except Exception:
            self.usage_day = db_usage
            
        self.last_check_date = today_date

    def is_available(self) -> bool:
        now_ts = time.time()
        today_date = datetime.now(timezone.utc).date()
        
        # Check for New Day Reset
        if today_date > self.last_check_date:
            print(f"DEBUG: New day detected ({today_date}). Resetting daily counts.")
            self.usage_day = 0
            self.last_check_date = today_date

        # Check Backoff (Temporary ban due to errors)
        if now_ts < self.banned_until:
            return False
            
        # Check/Reset Minute Limit
        if now_ts - self.minute_start_time >= 60:
            self.usage_minute = 0
            self.minute_start_time = now_ts
            
        # STRICT RPM CHECK
        if self.usage_minute >= self.rpm_limit:
            return False
            
        # STRICT RPD CHECK (Daily Limit)
        if self.usage_day >= self.rpd_limit:
            return False
            
        return True

    def mark_success(self) -> None:
        self.backoff = 0.0
        self.banned_until = 0.0
        self.success += 1
        self.usage_minute += 1
        self.usage_day += 1
        
        # Async update to Supabase (Fire and Forget)
        # We use a background task to avoid blocking the response
        asyncio.create_task(self.update_usage_in_db())

    async def update_usage_in_db(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            return
        try:
            # STRICT UPDATE: Update DB on EVERY successful request
            # No batching, no skipping. We need accurate counts.
            
            def _update():
                try:
                    client = create_client(SUPABASE_URL, SUPABASE_KEY)
                    # Also update last_used_at to track activity
                    client.table("gemini_api_keys").update({
                        "usage_count_day": self.usage_day,
                        "last_used_at": time.strftime('%Y-%m-%d %H:%M:%S')
                    }).eq("key", self.key).execute()
                except Exception as e:
                    print(f"DB Update Error for key {self.key[:8]}: {e}")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _update)
            
        except Exception:
            pass

    def mark_failure(self) -> None:
        if self.backoff <= 0:
            self.backoff = BACKOFF_MIN
        else:
            self.backoff = min(BACKOFF_MAX, self.backoff * 2.0)
        self.banned_until = time.monotonic() + self.backoff
        self.fail += 1


class KeyPool:
    def __init__(self, keys_data: List[Dict[str, Any]]):
        self.states: List[KeyState] = [KeyState(k) for k in keys_data]
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


POOL = KeyPool(KEYS_DATA)

# -------------------------
# Upstream streaming
# -------------------------
async def stream_from_upstream(method: str, url: str, headers: Dict[str, str], content: Optional[bytes], key_state: KeyState, timeout: int = 300):
    # Determine which proxy to use
    proxy_url = None
    
    # Only use proxy for actual LLM calls (chat/completions), not for model listing
    # But since stream_from_upstream is mostly used for chat/completions, we assume we need proxy here.
    # However, let's double check if we can skip proxy for non-critical calls?
    # Actually, stream_from_upstream is invoked by try_forward_to_upstream when is_stream=True.
    # And try_forward_to_upstream is called by /chat/completions mostly.
    
    if USE_FREE_PROXY and FREE_PROXY_LIB_INSTALLED:
        proxy_url = await PROXY_MANAGER.get_proxy()
    
    # Fallback to configured VPN/Data Center proxy if no free proxy found
    if not proxy_url and VPN_PROXY_URL:
        # Use our new dynamic rotation logic
        proxy_url = get_rotating_proxy_url()

    # Use proxy if configured, but verify=False to avoid SSL issues with some proxies
    transport = httpx.AsyncHTTPTransport(verify=False)
    try:
        # If proxy_url is None, httpx will use direct connection (or system proxy if env vars set)
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

async def try_forward_to_upstream(method: str, url: str, headers: Dict[str, str], content: Optional[bytes], is_stream: bool, key_state: KeyState, timeout: int = 300, use_proxy: bool = True):
    if is_stream:
        gen = stream_from_upstream(method, url, headers, content, key_state, timeout=timeout)
        return StreamingResponse(gen, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
    else:
        # Determine which proxy to use
        proxy_url = None
        
        if use_proxy:
            if USE_FREE_PROXY and FREE_PROXY_LIB_INSTALLED:
                proxy_url = await PROXY_MANAGER.get_proxy()
            
            # Fallback
            if not proxy_url and VPN_PROXY_URL:
                # Use our new dynamic rotation logic
                proxy_url = get_rotating_proxy_url()
        else:
            # Explicitly disable proxy for this request
            proxy_url = None

        # Use proxy if configured, but verify=False to avoid SSL issues with some proxies
        transport = httpx.AsyncHTTPTransport(verify=False)
        
        # Debug Log: Confirm Proxy Usage
        if proxy_url:
            # Mask password for security in logs
            safe_proxy = proxy_url.split("@")[-1] if "@" in proxy_url else "HIDDEN"
            print(f"DEBUG: Forwarding request via Proxy: ...@{safe_proxy}")
        else:
            print("DEBUG: Direct connection (No Proxy)")

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
# Auth Utilities
# -------------------------
def is_authenticated(request: Request) -> bool:
    """Check if user is logged in via cookie only."""
    session_token = request.cookies.get("admin_session")
    if session_token == ADMIN_TOKEN:
        return True
    
    # Disabled header/query token for admin dashboard access as requested
    # API endpoints still use Authorization header via validate_api_key middleware
    return False

# -------------------------
# Templates
# -------------------------
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Login - SalesmenChatbot AI</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0f172a; color: #e2e8f0; font-family: 'Inter', sans-serif; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); }
    </style>
</head>
<body class="h-screen flex items-center justify-center p-4">
    <div class="max-w-md w-full glass p-8 rounded-2xl shadow-2xl">
        <div class="text-center mb-8">
            <div class="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-xl mb-4 shadow-lg shadow-blue-500/20">
                <i class="fas fa-robot text-2xl text-white"></i>
            </div>
            <h1 class="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">SalesmenChatbot Admin</h1>
            <p class="text-slate-400 mt-2">Enter credentials to access dashboard</p>
        </div>

        <form action="/admin/login" method="POST" class="space-y-6">
            <div>
                <label class="block text-sm font-medium text-slate-300 mb-2">Username</label>
                <div class="relative">
                    <span class="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-500">
                        <i class="fas fa-user"></i>
                    </span>
                    <input type="text" name="username" required class="w-full pl-10 pr-4 py-3 bg-slate-900/50 border border-slate-700 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all text-white" placeholder="admin">
                </div>
            </div>
            <div>
                <label class="block text-sm font-medium text-slate-300 mb-2">Password</label>
                <div class="relative">
                    <span class="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-500">
                        <i class="fas fa-lock"></i>
                    </span>
                    <input type="password" name="password" required class="w-full pl-10 pr-4 py-3 bg-slate-900/50 border border-slate-700 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all text-white" placeholder="\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022">
                </div>
            </div>
            
            <button type="submit" class="w-full py-3 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white font-semibold rounded-xl shadow-lg shadow-blue-500/20 transform hover:-translate-y-0.5 active:translate-y-0 transition-all">
                Sign In
            </button>
        </form>
        
        <div class="mt-8 text-center text-xs text-slate-500">
            &copy; 2026 SalesmenChatbot AI Inc.
        </div>
    </div>
</body>
</html>
"""

# -------------------------
# Health Check & Frontend
# -------------------------
@APP.get("/", response_class=HTMLResponse)
async def root_redirect():
    # Redirect root directly to admin login
    return RedirectResponse(url="/admin/login")

@APP.get("/v1")
async def v1_root():
    # Show status message for /v1, but keep actual API endpoints secure
    return {
        "service": "SalesmenChatbot AI LLM",
        "status": "operational",
        "message": "SalesmenChatbot LLM is working. Please use /v1/chat/completions for API access."
    }

@APP.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/admin/login")
    return HTMLResponse(content=HTML_TEMPLATE, status_code=200)

@APP.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/admin")
    return HTMLResponse(content=LOGIN_TEMPLATE, status_code=200)

@APP.post("/admin/login")
async def login_handler(username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=303)
        # Set cookie valid for 30 days
        response.set_cookie(key="admin_session", value=ADMIN_TOKEN, max_age=2592000, httponly=True)
        return response
    return HTMLResponse(content=LOGIN_TEMPLATE.replace("Enter credentials", "<span style='color:#ef4444'>Invalid username or password</span>"), status_code=401)

@APP.get("/admin/logout")
async def logout_handler():
    response = RedirectResponse(url="/admin/login")
    response.delete_cookie("admin_session")
    return response

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
@APP.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(request: Request, full_path: str):
    # Only allow specific paths to use proxy logic
    # Everything else should be 404 locally without touching proxy
    
    # 1. Local routes (already handled by specific decorators above, but just in case)
    if full_path in ["", "health", "status", "reload-keys", "favicon.ico", "admin", "admin/login", "admin/logout", "v1"]:
        return JSONResponse(status_code=404, content={"error": "Not Found"})
        
    # Special Handling for GET /v1/chat/completions (Browser Access)
    if full_path.endswith("chat/completions") and request.method == "GET":
        return JSONResponse({
            "service": "SalesmenChatbot AI LLM",
            "status": "operational",
            "message": "SalesmenChatbot LLM is working. Please use POST method for API requests."
        })

    # 2. Allow only valid OpenAI/Gemini paths
    valid_prefixes = ["v1/chat/completions", "v1/models", "chat/completions", "models", "api/external/v1"]
    is_valid_path = any(full_path.startswith(p) for p in valid_prefixes)
    
    if not is_valid_path:
        # Block unknown paths LOCALLY. Do not forward to upstream.
        return JSONResponse(status_code=404, content={"error": "Path not found on this server."})

    upstream_url = map_incoming_to_upstream(full_path)
    content = await request.body()
    params = dict(request.query_params)
    is_stream = detect_stream_from_request(content if content else None, params)

    incoming_headers: Dict[str, str] = {k:v for k,v in request.headers.items() if k.lower() not in ("host","content-length","transfer-encoding","connection")}

    # -------------------------------------------------
    # HARD FILTER: Only use proxy for chat completions
    # -------------------------------------------------
    use_proxy = False
    if "chat/completions" in full_path:
        use_proxy = True

    # /models -> random available key
    p_normal = full_path[len("v1/"):] if full_path.startswith("v1/") else full_path
    if p_normal == "models" or p_normal.startswith("models/"):
        # Intercept /models response to return custom model name
        if request.method == "GET":
            return JSONResponse({
                "object": "list",
                "data": [
                    {
                        "id": "salesmanchatbot-pro",
                        "object": "model",
                        "created": 1677610602,
                        "owned_by": "salesmenchatbot-ai",
                        "permission": [],
                        "root": "salesmanchatbot-pro",
                        "parent": None
                    }
                ]
            })
            
        avail = [s for s in POOL.states if s.is_available()]
        key_state = random.choice(avail) if avail else min(POOL.states, key=lambda s: s.banned_until)
        headers = dict(incoming_headers)
        headers["Authorization"] = f"Bearer {key_state.key}"
        if not any(k.lower()=="content-type" for k in headers):
            ct = request.headers.get("content-type")
            headers["Content-Type"] = ct if ct else "application/json"
        # Disable proxy for models endpoint
        return await try_forward_to_upstream(request.method, upstream_url, headers, content, is_stream, key_state, use_proxy=False)

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
        if content:
            try:
                body_json = json.loads(content.decode())
                
                # Model Mapping: salesmanchatbot-pro -> gemini-2.5-flash
                if "model" in body_json:
                    if body_json["model"] in ["salesmanchatbot-pro", "salesmancahtbot-flash"]:
                        body_json["model"] = "gemini-2.5-flash" # Map to actual backend model
                
                # добавить thinking_config только если его нет
                if ENABLE_THINKING_CHAIN:
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
async def get_status(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return POOL.status()

@APP.post("/reload-keys")
async def reload_keys(request: Request):
    if not is_authenticated(request):
        raise HTTPException(401, "Unauthorized")
    global KEYS_DATA, POOL
    KEYS_DATA = load_keys_from_supabase()
    if not KEYS_DATA:
        KEYS_DATA = load_keys_from_file(KEYS_FILE)
    
    if not KEYS_DATA:
        return JSONResponse({"error": "No keys found"}, status_code=500)
        
    POOL = KeyPool(KEYS_DATA)
    return JSONResponse({"reloaded": True, "num_keys": len(KEYS_DATA)})

# -------------------------
# Run note:
# uvicorn main-openai:APP --host 127.0.0.1 --port 8000
# -------------------------
