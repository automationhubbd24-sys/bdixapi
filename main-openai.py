# main-openai.py
# FastAPI local OpenAI-compatible -> Gemini proxy with rotating keys
# Features: Dashboard, Key Management, Pagination, Security, Usage Tracking

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
import asyncpg
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel

# Load environment variables
load_dotenv()

# --- Config ---
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "changeme_local_only")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
POSTGRES_URL = os.getenv("POSTGRES_URL")
VPN_PROXY_URL = os.getenv("VPN_PROXY_URL")
UPSTREAM_BASE_GEMINI = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_RPM = 5
DEFAULT_RPH = 60 # Default RPH (Requests Per Hour)
DEFAULT_RPD = 20
BACKOFF_MIN = 5
BACKOFF_MAX = 600
ENABLE_THINKING_CHAIN = False
KEYS_FILE = "api_keys.txt"

# Global configuration store
GLOBAL_CONFIG = {
    "rpm": DEFAULT_RPM,
    "rph": DEFAULT_RPH,
    "rpd": DEFAULT_RPD
}

# --- Models ---
class ConfigUpdate(BaseModel):
    rpm: int
    rph: int
    rpd: int

class KeyCreate(BaseModel):
    api: str
    provider: str = "google"
    model: str = "default"
    status: str = "active"

class KeyUpdate(BaseModel):
    status: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None

# --- HTML Templates ---
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

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SalesmenChatbot Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        body { background-color: #0f172a; color: #e2e8f0; font-family: 'Inter', sans-serif; }
        .card { background-color: #1e293b; border-radius: 0.75rem; padding: 1.5rem; border: 1px solid #334155; }
        
        /* Custom Toast Styles */
        #toast-container { position: fixed; top: 1.5rem; right: 1.5rem; z-index: 1000; display: flex; flex-direction: column; gap: 0.75rem; }
        .toast { 
            background: #1e293b; border: 1px solid #334155; color: #e2e8f0; 
            padding: 1rem 1.25rem; border-radius: 0.75rem; min-width: 300px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
            display: flex; align-items: center; gap: 0.75rem;
            animation: slideIn 0.3s ease-out forwards;
        }
        @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .toast.error { border-left: 4px solid #f87171; }
        .toast.success { border-left: 4px solid #4ade80; }
        .toast.info { border-left: 4px solid #60a5fa; }
    </style>
</head>
<body class="p-8">
    <div id="toast-container"></div>
    <div class="max-w-6xl mx-auto">
        <header class="mb-8 flex justify-between items-center">
            <div>
                <h1 class="text-3xl font-bold text-white flex items-center gap-2">
                    <i data-lucide="server" class="h-8 w-8 text-blue-500"></i>
                    SalesmenChatbot API
                </h1>
                <p class="text-slate-400 mt-2">Secure Enterprise AI Gateway</p>
            </div>
            <div class="flex gap-4">
                <button onclick="checkHealth()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-white font-medium transition flex items-center gap-2">
                    <i data-lucide="activity" class="h-4 w-4"></i> Check Health
                </button>
                 <button onclick="reloadKeys()" class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-white font-medium transition flex items-center gap-2">
                    <i data-lucide="refresh-cw" class="h-4 w-4"></i> Reload Config
                </button>
                <a href="/admin/logout" class="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-white font-medium transition flex items-center gap-2">
                    <i data-lucide="log-out" class="h-4 w-4"></i> Logout
                </a>
            </div>
        </header>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="card">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-slate-400 text-sm">Active Nodes</p>
                        <h3 class="text-3xl font-bold text-white mt-2" id="key-count">--</h3>
                    </div>
                    <div class="p-2 bg-blue-500/10 rounded-lg"><i data-lucide="key" class="h-6 w-6 text-blue-500"></i></div>
                </div>
            </div>
            
            <!-- Global Limits Card -->
            <div class="card md:col-span-2">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-bold text-white flex items-center gap-2">
                        <i data-lucide="settings" class="h-5 w-5 text-blue-400"></i> Global Gemini Limits
                    </h3>
                    <button onclick="updateGlobalConfig()" class="text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1 rounded transition">Save Limits</button>
                </div>
                <div class="grid grid-cols-3 gap-4">
                    <div>
                        <label class="text-[10px] uppercase tracking-wider text-slate-500 block mb-1">RPM (Minute)</label>
                        <input type="number" id="global-rpm" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="text-[10px] uppercase tracking-wider text-slate-500 block mb-1">RPH (Hour)</label>
                        <input type="number" id="global-rph" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                    </div>
                    <div>
                        <label class="text-[10px] uppercase tracking-wider text-slate-500 block mb-1">RPD (Day)</label>
                        <input type="number" id="global-rpd" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-blue-500">
                    </div>
                </div>
            </div>
        </div>

        <div class="flex gap-2 mb-6 border-b border-slate-800 pb-1">
            <button id="tab-nodes" onclick="showTab('nodes')" class="px-6 py-2 text-sm font-bold transition-all border-b-2 border-blue-500 text-blue-500">Node Performance</button>
            <button id="tab-keys" onclick="showTab('keys')" class="px-6 py-2 text-sm font-bold transition-all border-b-2 border-transparent text-slate-400 hover:text-white">Key Management</button>
        </div>

        <div id="content-nodes" class="tab-content">
            <div class="card overflow-hidden mb-8 p-4"> <!-- Reduced padding -->
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-lg font-bold text-white flex items-center gap-2"> <!-- Smaller header -->
                        <i data-lucide="list" class="h-4 w-4 text-slate-400"></i> Node Performance
                    </h2>
                    <input type="text" id="node-search-input" onkeyup="renderKeys()" placeholder="Search API nodes..." class="bg-slate-800 border border-slate-700 text-white px-3 py-1.5 rounded-lg text-xs focus:outline-none focus:border-blue-500 w-48">
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="text-slate-400 text-xs border-b border-slate-700"> <!-- Smaller text -->
                                <th class="pb-2 pl-2">Node ID</th>
                                <th class="pb-2">Status</th>
                                <th class="pb-2">Success</th>
                                <th class="pb-2" title="Failed requests (Rate limits or API errors)">Failures <i data-lucide="help-circle" class="h-3 w-3 inline-block opacity-50"></i></th>
                                <th class="pb-2">Latency (avg)</th>
                            <th class="pb-2">Action</th>
                        </tr>
                    </thead>
                        <tbody id="keys-table-body" class="text-slate-300 text-sm"></tbody> <!-- Smaller text -->
                    </table>
                </div>
                <!-- Node Performance Pagination -->
                <div class="flex justify-between items-center mt-4 text-xs text-slate-500">
                    <div id="nodes-pagination-info">Showing 0 to 0 of 0 nodes</div>
                    <div class="flex gap-2">
                        <button onclick="changeNodesPage(-1)" id="prev-nodes-page" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded transition disabled:opacity-50">Prev</button>
                        <button onclick="changeNodesPage(1)" id="next-nodes-page" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 rounded transition disabled:opacity-50">Next</button>
                    </div>
                </div>
            </div>
        </div>

        <div id="content-keys" class="tab-content hidden">
            <div class="card overflow-hidden">
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold text-white flex items-center gap-2">
                        <i data-lucide="database" class="h-5 w-5 text-slate-400"></i> Key Management
                    </h2>
                    <div class="flex gap-4">
                         <input type="text" id="search-input" onkeyup="renderManagementTable()" placeholder="Search keys..." class="bg-slate-800 border border-slate-700 text-white px-3 py-2 rounded-lg text-sm focus:outline-none focus:border-blue-500">
                         <select id="provider-filter" onchange="renderManagementTable()" class="bg-slate-800 border border-slate-700 text-white px-3 py-2 rounded-lg text-sm focus:outline-none focus:border-blue-500">
                            <option value="all">All Providers</option>
                         </select>
                         <button onclick="addKey()" class="px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg text-white font-medium transition flex items-center gap-2">
                            <i data-lucide="plus" class="h-4 w-4"></i> Add Key
                        </button>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="text-slate-400 text-sm border-b border-slate-700">
                                <th class="pb-3 pl-2">ID</th>
                                <th class="pb-3">Provider</th>
                                <th class="pb-3">Model</th>
                                <th class="pb-3">API Key</th>
                                <th class="pb-3">Status</th>
                                <th class="pb-3">Usage</th>
                                <th class="pb-3">Action</th>
                            </tr>
                        </thead>
                        <tbody id="manage-keys-body" class="text-slate-300"></tbody>
                    </table>
                </div>
                <div class="flex justify-between items-center mt-6 text-sm text-slate-400">
                    <div id="pagination-info">Showing 0 to 0 of 0 keys</div>
                    <div class="flex gap-2">
                        <button onclick="changePage(-1)" id="prev-page" class="px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded-md transition disabled:opacity-50">Previous</button>
                        <div id="page-numbers" class="flex gap-1"></div>
                        <button onclick="changePage(1)" id="next-page" class="px-3 py-1 bg-slate-800 hover:bg-slate-700 rounded-md transition disabled:opacity-50">Next</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        lucide.createIcons();

        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            
            let icon = 'info';
            if(type === 'success') icon = 'check-circle';
            if(type === 'error') icon = 'alert-circle';
            
            toast.innerHTML = `
                <i data-lucide="${icon}" class="h-5 w-5 ${type === 'success' ? 'text-green-400' : type === 'error' ? 'text-red-400' : 'text-blue-400'}"></i>
                <span class="text-sm font-medium">${message}</span>
            `;
            container.appendChild(toast);
            lucide.createIcons();
            
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(100%)';
                toast.style.transition = 'all 0.3s ease-in';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

        let allKeys = [];
        let allNodes = []; // Global store for performance nodes
        let currentPage = 1;
        let currentNodesPage = 1; // For Node Performance table
        const keysPerPage = 10;
        let revealedNodeKeys = {}; // Store revealed keys for performance nodes
        let revealedKeys = {}; // Store revealed keys for management table

        function showTab(tab) {
            document.getElementById('content-nodes').classList.toggle('hidden', tab !== 'nodes');
            document.getElementById('content-keys').classList.toggle('hidden', tab !== 'keys');
            
            const btnNodes = document.getElementById('tab-nodes');
            const btnKeys = document.getElementById('tab-keys');
            
            if(tab === 'nodes') {
                btnNodes.className = "px-6 py-2 text-sm font-bold transition-all border-b-2 border-blue-500 text-blue-500";
                btnKeys.className = "px-6 py-2 text-sm font-bold transition-all border-b-2 border-transparent text-slate-400 hover:text-white";
            } else {
                btnNodes.className = "px-6 py-2 text-sm font-bold transition-all border-b-2 border-transparent text-slate-400 hover:text-white";
                btnKeys.className = "px-6 py-2 text-sm font-bold transition-all border-b-2 border-blue-500 text-blue-500";
            }
        }

        async function fetchStats() {
            try {
                // Fetch config first
                const configRes = await fetch('/admin/config');
                if(configRes.ok) {
                    const config = await configRes.json();
                    document.getElementById('global-rpm').value = config.rpm;
                    document.getElementById('global-rph').value = config.rph;
                    document.getElementById('global-rpd').value = config.rpd;
                }

                const res = await fetch('/status');
                if(!res.ok) { if(res.status === 401) window.location.href = '/admin/login'; return; }
                const data = await res.json();
                if(Array.isArray(data)) {
                    allNodes = data;
                    renderKeys();
                    document.getElementById('key-count').innerText = data.length;
                }
                fetchDBKeys();
            } catch (e) { console.error(e); }
        }

        async function updateGlobalConfig() {
            const rpm = parseInt(document.getElementById('global-rpm').value);
            const rph = parseInt(document.getElementById('global-rph').value);
            const rpd = parseInt(document.getElementById('global-rpd').value);
            
            try {
                const res = await fetch('/admin/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rpm, rph, rpd })
                });
                if(res.ok) {
                    showToast("Global limits updated successfully!", "success");
                    fetchStats();
                } else {
                    showToast("Failed to update limits", "error");
                }
            } catch(e) { showToast("Error: " + e.message, "error"); }
        }

        async function fetchDBKeys() {
            try {
                const res = await fetch('/admin/keys');
                if(res.status === 401) { window.location.href = '/admin/login'; return; }
                if(!res.ok) { renderManagementTable("Error: Failed to load keys"); return; }
                const data = await res.json();
                if(!Array.isArray(data)) { renderManagementTable("Error: Invalid format"); return; }
                allKeys = data;
                updateProviderFilter();
                renderManagementTable();
            } catch(e) { renderManagementTable("Error: " + e.message); }
        }

        function updateProviderFilter() {
            const select = document.getElementById('provider-filter');
            if(!select) return;
            const current = select.value || 'all';
            const providers = new Set();
            allKeys.forEach(k => { if(k.provider) providers.add(k.provider); });
            const options = ['all', ...Array.from(providers).sort()];
            select.innerHTML = options.map(p => `<option value="${p}">${p === 'all' ? 'All Providers' : p}</option>`).join('');
            select.value = options.includes(current) ? current : 'all';
        }

        function renderManagementTable(message = "") {
            const tbody = document.getElementById('manage-keys-body');
            const search = document.getElementById('search-input').value.toLowerCase();
            const provider = document.getElementById('provider-filter').value;
            const paginationInfo = document.getElementById('pagination-info');
            const prevBtn = document.getElementById('prev-page');
            const nextBtn = document.getElementById('next-page');
            const pageNumbers = document.getElementById('page-numbers');

            if(!tbody) return;
            tbody.innerHTML = '';
            if(pageNumbers) pageNumbers.innerHTML = '';

            if(message) {
                tbody.innerHTML = `<tr><td class="py-4 pl-2 text-sm text-red-400" colspan="7">${message}</td></tr>`;
                if(paginationInfo) paginationInfo.innerText = "Showing 0 to 0 of 0 keys";
                return;
            }

            const filtered = allKeys.filter(k => {
                const matchesProvider = provider === 'all' || (k.provider && k.provider.toLowerCase() === provider.toLowerCase());
                const keyText = k.api || "";
                const matchesSearch = keyText.toLowerCase().includes(search) || (k.provider && k.provider.toLowerCase().includes(search)) || (k.model && k.model.toLowerCase().includes(search));
                return matchesProvider && matchesSearch;
            });

            const totalKeys = filtered.length;
            const totalPages = Math.ceil(totalKeys / keysPerPage) || 1;
            if(currentPage > totalPages) currentPage = totalPages;
            if(currentPage < 1) currentPage = 1;

            const startIdx = (currentPage - 1) * keysPerPage;
            const endIdx = Math.min(startIdx + keysPerPage, totalKeys);
            const pageKeys = filtered.slice(startIdx, endIdx);

            if(totalKeys === 0) {
                tbody.innerHTML = `<tr><td class="py-4 pl-2 text-sm text-slate-400" colspan="7">No keys found</td></tr>`;
                if(paginationInfo) paginationInfo.innerText = "Showing 0 to 0 of 0 keys";
                return;
            }

            if(paginationInfo) paginationInfo.innerText = `Showing ${startIdx + 1} to ${endIdx} of ${totalKeys} keys`;
            if(prevBtn) prevBtn.disabled = currentPage === 1;
            if(nextBtn) nextBtn.disabled = currentPage === totalPages;

            pageKeys.forEach(k => {
                const tr = document.createElement('tr');
                tr.className = "border-b border-slate-800 hover:bg-slate-800/50 transition";
                const fullKey = k.api || "";
                const maskedKey = fullKey ? fullKey.substring(0, 8) + "***" + fullKey.substring(fullKey.length - 4) : "N/A";
                const reveal = revealedKeys[k.id];
                const showLabel = reveal ? "Hide" : "Show";
                
                tr.innerHTML = `
                    <td class="py-4 pl-2 text-sm text-slate-500">${k.id}</td>
                    <td class="py-4 text-sm text-white font-medium">${k.provider}</td>
                    <td class="py-4 text-sm text-slate-400">${k.model || '-'}</td>
                    <td class="py-4 font-mono text-sm text-slate-300">
                        <div class="flex items-center gap-2">
                            <span>${reveal ? fullKey : maskedKey}</span>
                        </div>
                    </td>
                    <td class="py-4 text-sm">
                        <span class="px-2 py-1 rounded-full text-[10px] font-bold uppercase ${k.status === 'active' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}">${k.status}</span>
                    </td>
                    <td class="py-4 text-sm text-slate-400 font-mono">${k.usage_today || 0}</td>
                    <td class="py-4 flex gap-2">
                         <button onclick="toggleReveal('${k.id}')" class="px-2 py-1 text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded">${showLabel}</button>
                         <button onclick="copyKey('${k.id}')" class="px-2 py-1 text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded">Copy</button>
                         <button onclick="deleteKey('${k.id}')" class="px-2 py-1 text-xs bg-red-900/20 text-red-400 rounded">Delete</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            lucide.createIcons();
        }

        async function toggleReveal(id) {
            if(revealedKeys[id]) { delete revealedKeys[id]; renderManagementTable(); }
            else {
                try {
                    const res = await fetch(`/admin/keys/id/${id}/reveal`);
                    const data = await res.json();
                    if(data.api) { revealedKeys[id] = data.api; renderManagementTable(); }
                } catch(e) { console.error(e); }
            }
        }

        async function copyToClipboard(text) {
            try {
                await navigator.clipboard.writeText(text);
                showToast("Key copied to clipboard!", "success");
            } catch (err) {
                console.error('Failed to copy: ', err);
                showToast("Failed to copy key", "error");
            }
        }

        async function copyKey(id) {
            try {
                const res = await fetch(`/admin/keys/id/${id}/reveal`);
                const data = await res.json();
                if(data.api) { copyToClipboard(data.api); }
            } catch(e) { showToast("Failed: " + e.message, "error"); }
        }

        async function deleteKey(id) {
            if(!confirm("Delete this key?")) return;
            try {
                await fetch(`/admin/keys/id/${id}`, { method: 'DELETE' });
                showToast("Key deleted successfully", "info");
                fetchDBKeys();
            } catch(e) { showToast("Failed: " + e.message, "error"); }
        }

        async function addKey() {
            const api = prompt("Enter API Key:");
            if(!api) return;
            const provider = prompt("Provider (google/gemini):", "google");
            
            try {
                const res = await fetch('/admin/keys', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ api, provider, status: 'active', model: 'default' })
                });
                if(res.status === 401) { window.location.href = '/admin/login'; return; }
                const data = await res.json();
                if(res.ok) {
                    showToast(data.message || "Key added successfully", "success");
                    fetchDBKeys();
                } else {
                    showToast(data.error || "Failed to add key", "error");
                }
            } catch(e) {
                showToast("Error adding key", "error");
            }
        }

        function changePage(delta) { currentPage += delta; renderManagementTable(); }
        function changeNodesPage(delta) {
            currentNodesPage += delta;
            renderKeys();
        }

        function renderKeys() {
            const tbody = document.getElementById('keys-table-body');
            const searchInput = document.getElementById('node-search-input');
            const search = searchInput ? searchInput.value.toLowerCase() : "";
            const info = document.getElementById('nodes-pagination-info');
            const prev = document.getElementById('prev-nodes-page');
            const next = document.getElementById('next-nodes-page');
            
            if(!tbody) return;
            
            const filtered = allNodes.filter(k => {
                return k.full_key.toLowerCase().includes(search) || k.key_preview.toLowerCase().includes(search);
            });

            const total = filtered.length;
            const totalPages = Math.ceil(total / keysPerPage) || 1;
            if(currentNodesPage > totalPages) currentNodesPage = totalPages;
            if(currentNodesPage < 1) currentNodesPage = 1;

            const start = (currentNodesPage - 1) * keysPerPage;
            const end = Math.min(start + keysPerPage, total);
            const pageItems = filtered.slice(start, end);

            if(info) info.innerText = `Showing ${total === 0 ? 0 : start + 1} to ${end} of ${total} nodes`;
            if(prev) prev.disabled = currentNodesPage === 1;
            if(next) next.disabled = currentNodesPage === totalPages;

            tbody.innerHTML = pageItems.map(k => {
                const isRevealed = revealedNodeKeys[k.full_key];
                const displayKey = isRevealed ? k.full_key : k.key_preview;
                
                return `
                <tr class="border-b border-slate-800 hover:bg-slate-800/50 transition">
                    <td class="py-3 pl-2 font-mono text-xs text-slate-400">
                        <div class="flex items-center gap-2">
                            <span>${displayKey}</span>
                        </div>
                    </td>
                    <td class="py-3 text-xs ${k.available_in > 0 ? "text-red-400" : "text-green-400"} font-medium">${k.available_in > 0 ? `Limited (${k.available_in}s)` : "Ready"}</td>
                    <td class="py-3 text-xs text-green-400">${k.success}</td>
                    <td class="py-3 text-xs text-red-400">${k.fail}</td>
                    <td class="py-3 text-xs text-slate-500">-</td>
                    <td class="py-3">
                        <div class="flex items-center gap-2">
                            <button onclick="toggleNodeReveal('${k.full_key}')" class="p-1 hover:text-blue-400 transition" title="${isRevealed ? 'Hide' : 'Show'} Key">
                                <i data-lucide="${isRevealed ? 'eye-off' : 'eye'}" class="h-3 w-3"></i>
                            </button>
                            <button onclick="copyToClipboard('${k.full_key}')" class="p-1 hover:text-blue-400 transition" title="Copy Full Key">
                                <i data-lucide="copy" class="h-3 w-3"></i>
                            </button>
                        </div>
                    </td>
                </tr>`;
            }).join('');
            lucide.createIcons();
        }

        function toggleNodeReveal(fullKey) {
            if(revealedNodeKeys[fullKey]) delete revealedNodeKeys[fullKey];
            else revealedNodeKeys[fullKey] = true;
            renderKeys();
        }
        
        async function checkHealth() { const res = await fetch('/health'); const data = await res.json(); alert(JSON.stringify(data, null, 2)); }
        async function reloadKeys() { if(confirm("Reload keys?")) { await fetch('/reload-keys', { method: 'POST' }); fetchStats(); } }

        fetchStats();
        setInterval(fetchStats, 5000);
    </script>
</body>
</html>
"""

# --- Logic ---
class KeyState:
    def __init__(self, key_data: Dict[str, Any]):
        self.key: str = key_data["key"]
        self.backoff: float = 0.0
        self.banned_until: float = 0.0
        self.success: int = 0
        self.fail: int = 0
        # Sliding Window timestamps
        self.requests_minute: List[float] = []
        self.requests_hour: List[float] = []
        self.requests_day: List[float] = []
        self.usage_day_db = key_data.get("usage_day", 0) # Base usage from DB

    def is_available(self) -> bool:
        now = time.time()
        
        # 1. Check Banned/Backoff
        if now < self.banned_until:
            return False

        # 2. Cleanup old timestamps & Check Limits
        rpm_limit = GLOBAL_CONFIG.get("rpm", DEFAULT_RPM)
        rph_limit = GLOBAL_CONFIG.get("rph", DEFAULT_RPH)
        rpd_limit = GLOBAL_CONFIG.get("rpd", DEFAULT_RPD)

        # Minute cleanup
        self.requests_minute = [t for t in self.requests_minute if now - t < 60]
        if len(self.requests_minute) >= rpm_limit:
            return False

        # Hour cleanup
        self.requests_hour = [t for t in self.requests_hour if now - t < 3600]
        if len(self.requests_hour) >= rph_limit:
            return False

        # Day cleanup (24 hours)
        self.requests_day = [t for t in self.requests_day if now - t < 86400]
        # Total day usage = sliding window count + DB starting usage (if window is fresh)
        if (len(self.requests_day) + self.usage_day_db) >= rpd_limit:
            return False
        
        return True

    def mark_picked(self):
        now = time.time()
        self.requests_minute.append(now)
        self.requests_hour.append(now)
        self.requests_day.append(now)

    def mark_success(self):
        self.banned_until = 0.0
        self.success += 1
        # Update DB usage_today
        asyncio.create_task(self.update_db())

    async def update_db(self):
        if POSTGRES_URL:
            try:
                conn = await asyncpg.connect(POSTGRES_URL)
                # usage_today in DB should reflect the total picked in last 24h cycle
                total_today = len(self.requests_day) + self.usage_day_db
                await conn.execute("UPDATE api_list SET usage_today = $1, last_used_at = NOW() WHERE api = $2", total_today, self.key)
                await conn.close()
            except: pass

    def mark_failure(self):
        self.backoff = BACKOFF_MIN if self.backoff <= 0 else min(BACKOFF_MAX, self.backoff * 2.0)
        self.banned_until = time.monotonic() + self.backoff
        self.fail += 1

class KeyPool:
    def __init__(self, keys_data: List[Dict[str, Any]]):
        self.states = [KeyState(k) for k in keys_data]
        self.idx = 0
        self.lock = asyncio.Lock()

    async def next_available(self) -> Optional[KeyState]:
        async with self.lock:
            for _ in range(len(self.states)):
                st = self.states[self.idx]
                self.idx = (self.idx + 1) % len(self.states)
                if st.is_available(): 
                    st.mark_picked() # Mark as used immediately to avoid parallel overflow
                    return st
            return None

    def status(self):
        now = time.monotonic()
        return [{
            "full_key": s.key,
            "key_preview": s.key[:8] + "...",
            "available_in": max(0, round(s.banned_until - now, 2)),
            "success": s.success,
            "fail": s.fail
        } for s in self.states]

POOL = KeyPool([])

# --- Endpoints ---
APP = FastAPI()

def is_authenticated(request: Request):
    return request.cookies.get("admin_session") == ADMIN_TOKEN

@APP.on_event("startup")
async def startup():
    global POOL, GLOBAL_CONFIG
    if POSTGRES_URL:
        try:
            conn = await asyncpg.connect(POSTGRES_URL)
            # 1. Create global_config table if not exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS global_config (
                    key TEXT PRIMARY KEY,
                    value JSONB
                )
            """)
            
            # 2. Load or Init Global Limits
            config_row = await conn.fetchrow("SELECT value FROM global_config WHERE key = 'gemini_limits'")
            if config_row:
                GLOBAL_CONFIG.update(json.loads(config_row['value']))
            else:
                await conn.execute("INSERT INTO global_config (key, value) VALUES ('gemini_limits', $1)", json.dumps(GLOBAL_CONFIG))
            
            # 3. Load Keys
            rows = await conn.fetch("SELECT api as key, usage_today as usage_day FROM api_list WHERE (provider ILIKE '%google%' OR provider ILIKE '%gemini%') AND status = 'active'")
            await conn.close()
            POOL = KeyPool([dict(r) for r in rows])
        except Exception as e:
            print(f"Startup Error: {e}")

@APP.get("/admin/config")
async def get_config(request: Request):
    return GLOBAL_CONFIG

@APP.post("/admin/config")
async def update_config(update: ConfigUpdate, request: Request):
    global GLOBAL_CONFIG
    GLOBAL_CONFIG["rpm"] = update.rpm
    GLOBAL_CONFIG["rph"] = update.rph
    GLOBAL_CONFIG["rpd"] = update.rpd
    
    if POSTGRES_URL:
        try:
            conn = await asyncpg.connect(POSTGRES_URL)
            await conn.execute("UPDATE global_config SET value = $1 WHERE key = 'gemini_limits'", json.dumps(GLOBAL_CONFIG))
            await conn.close()
        except: pass
    return {"message": "Config updated"}

@APP.post("/admin/keys")
async def add_key(key: KeyCreate):
    if not POSTGRES_URL: return {"error": "No DB"}
    conn = await asyncpg.connect(POSTGRES_URL)
    await conn.execute("INSERT INTO api_list (provider, model, api, status, usage_today) VALUES ($1, $2, $3, $4, 0)", key.provider, key.model, key.api, key.status)
    await conn.close()
    return {"message": "Key added"}

@APP.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/admin") or path in ["/status", "/reload-keys"]:
        if path not in ["/admin/login", "/admin/logout"] and not is_authenticated(request):
            if path.startswith("/admin/keys") or path.startswith("/admin/config"): return JSONResponse({"error": "Unauthorized"}, 401)
            return RedirectResponse("/admin/login")
    return await call_next(request)

@APP.get("/", response_class=RedirectResponse)
async def root(): return "/admin/login"

@APP.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request): return RedirectResponse("/admin")
    return LOGIN_TEMPLATE

@APP.post("/admin/login")
async def login_handler(username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        resp = RedirectResponse("/admin", 303)
        resp.set_cookie("admin_session", ADMIN_TOKEN, httponly=True)
        return resp
    return HTMLResponse(LOGIN_TEMPLATE.replace("Enter credentials", "<span class='text-red-500'>Invalid</span>"), 401)

@APP.get("/admin/logout")
async def logout():
    resp = RedirectResponse("/admin/login")
    resp.delete_cookie("admin_session")
    return resp

@APP.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request): return HTML_TEMPLATE

@APP.get("/status")
async def status(): return POOL.status()

@APP.get("/admin/keys")
async def get_keys():
    if not POSTGRES_URL: return []
    conn = await asyncpg.connect(POSTGRES_URL)
    rows = await conn.fetch("SELECT id, provider, model, api, status, usage_today FROM api_list WHERE provider ILIKE '%google%' OR provider ILIKE '%gemini%' ORDER BY id DESC")
    await conn.close()
    return [dict(r) for r in rows]

@APP.get("/admin/keys/id/{key_id}/reveal")
async def reveal_key(key_id: int):
    conn = await asyncpg.connect(POSTGRES_URL)
    row = await conn.fetchrow("SELECT api FROM api_list WHERE id = $1", key_id)
    await conn.close()
    return {"api": row["api"]} if row else {"error": "Not found"}

@APP.delete("/admin/keys/id/{key_id}")
async def delete_key(key_id: int):
    conn = await asyncpg.connect(POSTGRES_URL)
    await conn.execute("DELETE FROM api_list WHERE id = $1", key_id)
    await conn.close()
    return {"message": "Deleted"}

@APP.get("/health")
async def health(): return {"status": "operational"}

@APP.post("/reload-keys")
async def reload():
    await startup()
    return {"reloaded": True}

@APP.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(request: Request, full_path: str):
    if not any(full_path.startswith(p) for p in ["v1/chat/completions", "v1/models", "chat/completions", "models"]):
        return JSONResponse({"error": "Not found"}, 404)
    
    # Model List intercept
    if "models" in full_path and request.method == "GET":
        return {"object": "list", "data": [{"id": "salesmanchatbot-pro", "object": "model", "owned_by": "salesmenchatbot-ai"}]}

    key_state = await POOL.next_available()
    if not key_state: return JSONResponse({"error": "No keys available"}, 429)

    headers = {k:v for k,v in request.headers.items() if k.lower() not in ("host","content-length","transfer-encoding","connection")}
    headers["Authorization"] = f"Bearer {key_state.key}"
    
    # Read and modify body to ensure valid Gemini model
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
        # Map our custom model name to a real Gemini model
        if "model" in body_json:
            # You can change 'gemini-1.5-flash' to 'gemini-1.5-pro' if needed
            body_json["model"] = "gemini-1.5-flash"
        content = json.dumps(body_json).encode('utf-8')
    except:
        content = body_bytes

    is_stream = "stream" in str(content).lower() or request.query_params.get("stream") == "true"
    
    url = f"{UPSTREAM_BASE_GEMINI}/openai/{full_path.replace('v1/', '')}"
    
    async with httpx.AsyncClient(timeout=300, verify=False, proxy=VPN_PROXY_URL) as client:
        try:
            if is_stream:
                async def stream_gen():
                    # Keep client alive for the duration of the stream
                    async with httpx.AsyncClient(timeout=300, verify=False, proxy=VPN_PROXY_URL) as stream_client:
                        async with stream_client.stream(request.method, url, headers=headers, content=content) as upstream:
                            if upstream.status_code >= 400:
                                key_state.mark_failure()
                                err_body = await upstream.aread()
                                yield err_body
                            else:
                                key_state.mark_success()
                                async for chunk in upstream.aiter_bytes(): yield chunk
                return StreamingResponse(stream_gen(), media_type="text/event-stream")
            else:
                resp = await client.request(request.method, url, headers=headers, content=content)
                if resp.status_code >= 400:
                    key_state.mark_failure()
                    # Return error as JSON to help n8n understand
                    return JSONResponse(status_code=resp.status_code, content=resp.json() if "application/json" in resp.headers.get("content-type", "") else {"error": resp.text})
                
                key_state.mark_success()
                return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
        except Exception as e:
            key_state.mark_failure()
            return JSONResponse({"error": str(e)}, 502)
