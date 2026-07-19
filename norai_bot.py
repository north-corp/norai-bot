import os
import json
import random
import hashlib
import secrets
import logging
import threading
from datetime import datetime
from functools import wraps

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, redirect
import requests

# ============================================================
# CONFIG
# ============================================================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
FLASK_SECRET = os.getenv("FLASK_SECRET", secrets.token_hex(32))
PORT = int(os.getenv("PORT", 10000))
ACCOUNTS_BACKUP = os.getenv("ACCOUNTS_BACKUP", "")

CEO_DISCORD_ID      = 779606576616964108
CHANNEL_BULLETINS   = 1528195910637719692
CHANNEL_FLIGHT_LOGS = 1528173868215177286
CHANNEL_ARRIVALS    = 1528169291873255434
GUILD_ID            = 1527555743413440533

FLEET_JSON_URL = "https://north-corp.github.io/northcorp/fleet.json"
ACCOUNTS_FILE  = "accounts.json"

ceo_status = "At HQ, Czerny Landing"

QUOTES = [
    '"No job too large or too remote."',
    '"Coffee\'s cold. Time to fly."',
    '"Deliver on time. That\'s the NORTHCORP way."',
    '"Work hard. Keep your word. Everything else sorts itself out."',
    '"A full cargo hold is an honest day\'s work."',
    '"The galaxy doesn\'t owe you a thing. Haul it anyway."',
    '"If the hold ain\'t full, the run ain\'t done."',
    '"Czerny Landing isn\'t Tethlon. But it\'ll do for now."',
    '"One day I\'ll park a carrier over Tethlon. Until then, I haul."',
    '"Trust is earned one delivery at a time."',
]

DEFAULT_ROLES = {
    "ceo":   {"level": 100, "label": "CEO"},
    "admin": {"level": 50,  "label": "Administrator"},
    "staff": {"level": 10,  "label": "Staff"},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NORAI] %(levelname)s: %(message)s"
)
logger = logging.getLogger("NORAI")

# ============================================================
# ACCOUNT MANAGEMENT
# ============================================================

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return {"roles": dict(DEFAULT_ROLES), "users": {}}
    with open(ACCOUNTS_FILE, "r") as f:
        data = json.load(f)
    if "roles" not in data:
        data["roles"] = dict(DEFAULT_ROLES)
    return data

def save_accounts(data):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def hash_password(password):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"

def check_password(password, stored):
    salt, h = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h

def get_user_level(username, data=None):
    if data is None:
        data = load_accounts()
    user = data["users"].get(username)
    if not user:
        return 0
    role_name = user.get("role", "staff")
    role = data["roles"].get(role_name, {})
    return role.get("level", 0)

def get_user_role_label(username, data=None):
    if data is None:
        data = load_accounts()
    user = data["users"].get(username)
    if not user:
        return "Unknown"
    role_name = user.get("role", "staff")
    role = data["roles"].get(role_name, {})
    return role.get("label", role_name)

def ensure_defaults():
    data = load_accounts()

    # Ensure default roles exist
    for name, info in DEFAULT_ROLES.items():
        if name not in data["roles"]:
            data["roles"][name] = info

    # Ensure CEO account exists
    if "k.north" not in data["users"]:
        data["users"]["k.north"] = {
            "password_hash": hash_password("tethlon"),
            "role": "ceo",
            "discord_id": str(CEO_DISCORD_ID)
        }
        logger.info("Created default CEO account (username: k.north). CHANGE THE PASSWORD.")
    else:
        # Ensure CEO role is locked — in case someone tampered with accounts.json
        data["users"]["k.north"]["role"] = "ceo"

    save_accounts(data)

    # Restore from backup if env var is set and local file only has defaults
    if ACCOUNTS_BACKUP:
        try:
            backup = json.loads(ACCOUNTS_BACKUP)
            backup_users = len(backup.get("users", {}))
            local_users = len(data["users"])
            if backup_users > local_users:
                logger.info(f"Restoring accounts from backup ({backup_users} users vs {local_users} local).")
                data = backup
                # Still enforce roles
                if "roles" not in data:
                    data["roles"] = dict(DEFAULT_ROLES)
                data["users"]["k.north"]["role"] = "ceo"
                save_accounts(data)
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")

    return data

def get_backup_json():
    """Return the current accounts.json as a compact JSON string for backup."""
    data = load_accounts()
    return json.dumps(data)

# ============================================================
# FLASK APP
# ============================================================

flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect("/")
        return f(*args, **kwargs)
    return decorated

def min_level(level):
    """Decorator: require user role level >= given level."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "username" not in session:
                return redirect("/")
            lvl = get_user_level(session["username"])
            if lvl < level:
                return "Access denied. Insufficient clearance.", 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# -- Styles --
BASE_STYLE = """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0a; color: #c0c0c0;
    font-family: "Courier New", Courier, monospace;
    line-height: 1.6; font-size: 15px;
  }
  .container { max-width: 900px; margin: 0 auto; padding: 40px 24px; }
  h1 { color: #c45a1a; font-size: 28px; letter-spacing: 4px; margin-bottom: 20px; }
  h2 { color: #c45a1a; font-size: 18px; letter-spacing: 2px; margin: 30px 0 16px; }
  h3 { color: #e0e0e0; font-size: 15px; margin-bottom: 10px; }
  label { display: block; color: #888; font-size: 12px; letter-spacing: 1px; text-transform: uppercase; margin: 12px 0 4px; }
  input, select, textarea {
    background: #0f0f0f; color: #e0e0e0; border: 1px solid #1a1a1a;
    padding: 10px 14px; font-family: "Courier New", monospace; font-size: 14px; width: 100%; max-width: 500px;
  }
  input:focus, select:focus, textarea:focus { border-color: #c45a1a; outline: none; }
  .btn {
    display: inline-block; background: #c45a1a; color: #0a0a0a;
    font-weight: bold; font-size: 13px; letter-spacing: 2px; text-transform: uppercase;
    text-decoration: none; padding: 12px 28px; border: none; cursor: pointer;
    font-family: "Courier New", monospace; margin-top: 16px;
  }
  .btn:hover { background: #d9702a; }
  .btn-ghost {
    background: transparent; color: #c45a1a; border: 2px solid #c45a1a;
    font-family: "Courier New", monospace; font-weight: bold; font-size: 13px;
    letter-spacing: 2px; text-transform: uppercase; padding: 12px 28px; cursor: pointer;
  }
  .btn-ghost:hover { background: #1a1008; }
  .btn-sm { font-size: 11px; padding: 6px 14px; letter-spacing: 1px; }
  .btn-danger { background: #8b3030; color: #e0e0e0; border: none; }
  .btn-danger:hover { background: #a04040; }
  .result-box {
    background: #0f0f0f; border: 1px solid #1a1a1a; padding: 24px; margin-top: 20px;
  }
  .result-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px;
  }
  .result-item label { color: #666; font-size: 10px; }
  .result-item .value { color: #e0e0e0; font-size: 18px; font-weight: bold; }
  .value-profit { color: #6a9a5b !important; }
  .value-cost { color: #a04040 !important; }
  .value-neutral { color: #c4a040 !important; }
  .flash { padding: 12px 16px; margin-bottom: 16px; border: 1px solid; }
  .flash-error { border-color: #a04040; color: #c06060; background: #150808; }
  .flash-ok { border-color: #5c8a5c; color: #6a9a5b; background: #081008; }
  .nav { display: flex; gap: 24px; margin-bottom: 30px; border-bottom: 1px solid #1a1a1a; padding-bottom: 12px; }
  .nav a { color: #888; text-decoration: none; font-size: 13px; letter-spacing: 1px; text-transform: uppercase; }
  .nav a:hover, .nav a.active { color: #c45a1a; }
  .nav .logout { margin-left: auto; color: #666; }
  .user-bar { color: #666; font-size: 12px; margin-bottom: 8px; }
  .mode-tabs { display: flex; gap: 0; margin-bottom: 20px; }
  .mode-tab {
    background: #0f0f0f; color: #888; border: 1px solid #1a1a1a;
    padding: 10px 20px; cursor: pointer; font-family: "Courier New", monospace;
    font-size: 13px; letter-spacing: 1px; text-transform: uppercase;
  }
  .mode-tab.active { background: #1a1008; color: #c45a1a; border-color: #c45a1a; }
  .inline { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #1a1a1a; font-size: 13px; }
  th { color: #666; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
  .hidden { display: none; }
  .backup-box {
    background: #0f0f0f; border: 1px solid #2a2a0a; padding: 16px;
    max-height: 200px; overflow-y: auto; font-size: 11px; color: #888;
    word-break: break-all; white-space: pre-wrap; margin-top: 8px;
  }
  @media (max-width: 600px) {
    .container { padding: 20px 16px; }
    .result-grid { grid-template-columns: 1fr; }
  }
</style>
"""

# -- Login --
@flask_app.route("/", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").lower().strip()
        password = request.form.get("password", "")
        data = load_accounts()
        user = data["users"].get(username)
        if user and check_password(password, user["password_hash"]):
            session["username"] = username
            return redirect("/calculator")
        error = "Invalid credentials."

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NORAI Trade Calculator — Login</title>{BASE_STYLE}
</head><body>
<div class="container" style="max-width:460px;padding-top:80px;">
<h1>NORAI Trade Calculator</h1>
<p style="color:#888;margin-bottom:24px;">NORTHCORP employee access. Authorized personnel only.</p>
{"<p class='flash flash-error'>" + error + "</p>" if error else ""}
<form method="POST">
<label>Username</label><input type="text" name="username" required>
<label>Password</label><input type="password" name="password" required>
<button type="submit" class="btn">Log In</button>
</form>
<p style="color:#666;font-size:12px;margin-top:24px;">No account? Contact CEO North.</p>
</div></body></html>"""

# -- Logout --
@flask_app.route("/logout")
def logout_page():
    session.clear()
    return redirect("/")

# -- Calculator --
@flask_app.route("/calculator")
@login_required
def calculator_page():
    data = load_accounts()
    user_level = get_user_level(session["username"], data)
    role_label = get_user_role_label(session["username"], data)
    is_admin = user_level >= 50

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NORAI Trade Calculator</title>{BASE_STYLE}
</head><body>
<div class="container">
<div class="user-bar">Logged in as: <strong style="color:#c45a1a;">{session['username']}</strong> [{role_label}]</div>
<div class="nav">
  <a href="/calculator" class="active">Calculator</a>
  {"<a href='/admin'>Admin</a>" if is_admin else ""}
  <a href="/settings">Settings</a>
  <a href="/logout" class="logout">Logout</a>
</div>

<h1>Trade Calculator</h1>

<div class="mode-tabs">
  <div class="mode-tab active" id="tab-pilot" onclick="switchMode('pilot')">Pilot Mode</div>
  <div class="mode-tab" id="tab-carrier" onclick="switchMode('carrier')">Carrier Mode</div>
</div>

<div id="mode-pilot">
<p style="color:#888;margin-bottom:16px;">Calculate profit for an individual pilot buying from a station and selling to a destination (station or carrier).</p>
<label>Commodity</label><input type="text" id="p-commodity" placeholder="e.g. Gold">
<label>Buy Price (CR per ton)</label><input type="number" id="p-buy" placeholder="45000" step="1" min="0">
<label>Sell Price (CR per ton)</label><input type="number" id="p-sell" placeholder="48000" step="1" min="0">
<label>Tonnage</label><input type="number" id="p-tons" placeholder="720" step="1" min="0">
<label>Commission (CR per ton)</label><input type="number" id="p-comm" placeholder="5000" step="1" min="0">
<p style="color:#666;font-size:11px;">Flat per-ton commission taken by carrier.</p>
<button class="btn" onclick="calcPilot()">Calculate</button>

<div class="result-box hidden" id="pilot-result">
<h3>Results — <span id="p-commodity-out"></span></h3>
<div class="result-grid">
  <div class="result-item"><label>Total Buy Cost</label><div class="value value-cost" id="p-buy-cost"></div></div>
  <div class="result-item"><label>Total Sell Revenue</label><div class="value value-profit" id="p-sell-rev"></div></div>
  <div class="result-item"><label>Gross Margin</label><div class="value value-neutral" id="p-gross"></div></div>
  <div class="result-item"><label>Carrier Commission</label><div class="value value-cost" id="p-comm-out"></div></div>
  <div class="result-item"><label>Pilot Net Profit</label><div class="value value-profit" id="p-net"></div></div>
  <div class="result-item"><label>Profit per Ton</label><div class="value" id="p-ppt"></div></div>
</div>
</div>
</div>

<div id="mode-carrier" class="hidden">
<p style="color:#888;margin-bottom:16px;">Calculate full carrier operation: buy at source, pay loading pilots, jump, sell at destination, pay unloading pilots.</p>
<h2>Leg 1 — Loading</h2>
<label>Commodity</label><input type="text" id="c-commodity" placeholder="e.g. Gold">
<label>Buy Price (CR per ton — what carrier pays at source)</label><input type="number" id="c-buy" placeholder="45000" step="1" min="0">
<label>Tonnage</label><input type="number" id="c-tons" placeholder="720" step="1" min="0">
<label>Loading Pilot Commission (CR per ton)</label><input type="number" id="c-load-comm" placeholder="5000" step="1" min="0">
<p style="color:#666;font-size:11px;">Paid to pilots hauling cargo onto the carrier at the source station.</p>
<h2>Transit</h2>
<label>Tritium Cost for Jump (CR)</label><input type="number" id="c-trit" placeholder="0" step="1" min="0" value="0">
<p style="color:#666;font-size:11px;">Optional — cost of fuel for the carrier jump.</p>
<h2>Leg 2 — Unloading</h2>
<label>Sell Price (CR per ton — what carrier sells for at destination)</label><input type="number" id="c-sell" placeholder="48000" step="1" min="0">
<label>Unloading Pilot Commission (CR per ton)</label><input type="number" id="c-unload-comm" placeholder="5000" step="1" min="0">
<p style="color:#666;font-size:11px;">Paid to pilots hauling cargo off the carrier at the destination.</p>
<button class="btn" onclick="calcCarrier()">Calculate</button>

<div class="result-box hidden" id="carrier-result">
<h3>Results — <span id="c-commodity-out"></span></h3>
<div class="result-grid">
  <div class="result-item"><label>Total Acquisition Cost</label><div class="value value-cost" id="c-acq"></div></div>
  <div class="result-item"><label>Loading Pilot Costs</label><div class="value value-cost" id="c-load-cost"></div></div>
  <div class="result-item"><label>Tritium Fuel Cost</label><div class="value value-cost" id="c-trit-out"></div></div>
  <div class="result-item"><label>Total Sell Revenue</label><div class="value value-profit" id="c-sell-rev"></div></div>
  <div class="result-item"><label>Unloading Pilot Costs</label><div class="value value-cost" id="c-unload-cost"></div></div>
  <div class="result-item"><label>Carrier Net Profit</label><div class="value value-profit" id="c-net"></div></div>
</div>
<h3>Per-Pilot Breakdown</h3>
<div class="result-grid">
  <div class="result-item"><label>Loading Pilot (per pilot, full load)</label><div class="value value-profit" id="c-load-per"></div></div>
  <div class="result-item"><label>Unloading Pilot (per pilot, full load)</label><div class="value value-profit" id="c-unload-per"></div></div>
  <div class="result-item"><label>Carrier Profit per Ton</label><div class="value" id="c-ppt"></div></div>
</div>
</div>
</div>

<h2 style="margin-top:40px;">Inara Price Lookup</h2>
<p style="color:#888;margin-bottom:12px;">Search live commodity prices via your personal Inara API key. Your key stays in your browser — never stored on the server.</p>
<label>Inara API Key</label>
<input type="password" id="inara-key" placeholder="Paste your Inara API key...">
<p style="color:#666;font-size:11px;margin-bottom:12px;">Saved to this browser only. Get yours at inara.cz → Settings → API.</p>
<label>Search Commodity</label>
<div class="inline">
  <input type="text" id="inara-search" placeholder="e.g. gold" style="flex:1;">
  <button class="btn btn-sm" onclick="searchInara()" style="margin-top:0;">Search</button>
</div>
<div id="inara-result" style="margin-top:16px;"></div>
</div>

<script>
function switchMode(mode) {{
  document.getElementById('tab-pilot').classList.toggle('active', mode==='pilot');
  document.getElementById('tab-carrier').classList.toggle('active', mode==='carrier');
  document.getElementById('mode-pilot').classList.toggle('hidden', mode!=='pilot');
  document.getElementById('mode-carrier').classList.toggle('hidden', mode!=='carrier');
}}
function fmt(n) {{ return n.toLocaleString('en-US') + ' CR'; }}
function calcPilot() {{
  var buy = parseFloat(document.getElementById('p-buy').value) || 0;
  var sell = parseFloat(document.getElementById('p-sell').value) || 0;
  var tons = parseFloat(document.getElementById('p-tons').value) || 0;
  var comm = parseFloat(document.getElementById('p-comm').value) || 0;
  var commName = document.getElementById('p-commodity').value || 'Commodity';
  var buyCost = buy * tons;
  var sellRev = sell * tons;
  var gross = sellRev - buyCost;
  var commAmt = comm * tons;
  var net = gross - commAmt;
  var ppt = tons > 0 ? net / tons : 0;
  document.getElementById('p-commodity-out').textContent = commName;
  document.getElementById('p-buy-cost').textContent = fmt(Math.round(buyCost));
  document.getElementById('p-sell-rev').textContent = fmt(Math.round(sellRev));
  document.getElementById('p-gross').textContent = fmt(Math.round(gross));
  document.getElementById('p-comm-out').textContent = fmt(Math.round(commAmt));
  document.getElementById('p-net').textContent = fmt(Math.round(net));
  document.getElementById('p-ppt').textContent = fmt(Math.round(ppt));
  document.getElementById('pilot-result').classList.remove('hidden');
}}
function calcCarrier() {{
  var buy = parseFloat(document.getElementById('c-buy').value) || 0;
  var tons = parseFloat(document.getElementById('c-tons').value) || 0;
  var loadComm = parseFloat(document.getElementById('c-load-comm').value) || 0;
  var trit = parseFloat(document.getElementById('c-trit').value) || 0;
  var sell = parseFloat(document.getElementById('c-sell').value) || 0;
  var unloadComm = parseFloat(document.getElementById('c-unload-comm').value) || 0;
  var commName = document.getElementById('c-commodity').value || 'Commodity';
  var acqCost = buy * tons;
  var loadCost = loadComm * tons;
  var sellRev = sell * tons;
  var unloadCost = unloadComm * tons;
  var net = sellRev - acqCost - loadCost - trit - unloadCost;
  var loadPer = loadComm * tons;
  var unloadPer = unloadComm * tons;
  var ppt = tons > 0 ? net / tons : 0;
  document.getElementById('c-commodity-out').textContent = commName;
  document.getElementById('c-acq').textContent = fmt(Math.round(acqCost));
  document.getElementById('c-load-cost').textContent = fmt(Math.round(loadCost));
  document.getElementById('c-trit-out').textContent = fmt(Math.round(trit));
  document.getElementById('c-sell-rev').textContent = fmt(Math.round(sellRev));
  document.getElementById('c-unload-cost').textContent = fmt(Math.round(unloadCost));
  document.getElementById('c-net').textContent = fmt(Math.round(net));
  document.getElementById('c-load-per').textContent = fmt(Math.round(loadPer));
  document.getElementById('c-unload-per').textContent = fmt(Math.round(unloadPer));
  document.getElementById('c-ppt').textContent = fmt(Math.round(ppt));
  document.getElementById('carrier-result').classList.remove('hidden');
}}
async function searchInara() {{
  var key = document.getElementById('inara-key').value.trim();
  var query = document.getElementById('inara-search').value.trim();
  var resultDiv = document.getElementById('inara-result');
  if (!key) {{ resultDiv.innerHTML = '<p class="flash flash-error">Enter your Inara API key first.</p>'; return; }}
  if (!query) {{ resultDiv.innerHTML = '<p class="flash flash-error">Enter a commodity to search.</p>'; return; }}
  localStorage.setItem('inara_api_key', key);
  resultDiv.innerHTML = '<p style="color:#888;">Searching Inara...</p>';
  try {{
    var resp = await fetch('/api/inara-proxy', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ api_key: key, commodity: query }})
    }});
    var data = await resp.json();
    if (data.error) {{ resultDiv.innerHTML = '<p class="flash flash-error">' + data.error + '</p>'; return; }}
    if (!data.listings || data.listings.length === 0) {{
      resultDiv.innerHTML = '<p style="color:#888;">No listings found for "' + query + '".</p>'; return;
    }}
    var html = '<table><thead><tr><th>Station</th><th>System</th><th>Buy/Sell</th><th>Price</th><th>Supply</th><th>Updated</th></tr></thead><tbody>';
    data.listings.forEach(function(l) {{
      html += '<tr>';
      html += '<td>' + l.station + '</td><td>' + l.system + '</td>';
      html += '<td style="color:' + (l.is_sell ? '#a04040' : '#6a9a5b') + ';">' + (l.is_sell ? 'Sell' : 'Buy') + '</td>';
      html += '<td>' + l.price.toLocaleString() + ' CR</td><td>' + l.supply.toLocaleString() + '</td>';
      html += '<td style="color:#666;">' + l.updated + '</td></tr>';
    }});
    html += '</tbody></table>';
    resultDiv.innerHTML = html;
  }} catch(e) {{ resultDiv.innerHTML = '<p class="flash flash-error">Network error. Try again.</p>'; }}
}}
window.onload = function() {{
  var saved = localStorage.getItem('inara_api_key');
  if (saved) document.getElementById('inara-key').value = saved;
}};
</script>
</body></html>"""

# -- Settings --
@flask_app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    msg = None
    if request.method == "POST":
        data = load_accounts()
        user = data["users"].get(session["username"])
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        if user and check_password(current_pw, user["password_hash"]):
            user["password_hash"] = hash_password(new_pw)
            save_accounts(data)
            msg = ("ok", "Password changed.")
        else:
            msg = ("error", "Current password is incorrect.")

    role_label = get_user_role_label(session["username"])
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Settings — NORAI Trade Calculator</title>{BASE_STYLE}
</head><body>
<div class="container">
<div class="user-bar">Logged in as: <strong style="color:#c45a1a;">{session['username']}</strong> [{role_label}]</div>
<div class="nav">
  <a href="/calculator">Calculator</a>
  <a href="/settings" class="active">Settings</a>
  <a href="/logout" class="logout">Logout</a>
</div>
<h1>Settings</h1>
{"<p class='flash flash-" + msg[0] + "'>" + msg[1] + "</p>" if msg else ""}
<form method="POST">
<h2>Change Password</h2>
<label>Current Password</label><input type="password" name="current_password" required>
<label>New Password</label><input type="password" name="new_password" required>
<button type="submit" class="btn">Update Password</button>
</form>
<h2>Inara API Key</h2>
<p style="color:#888;">Your Inara API key is stored in this browser only. The server never sees it except when relaying a search request — and it is never written to disk.</p>
<p style="color:#666;font-size:12px;">To update it, return to the Calculator page and enter a new key.</p>
</div></body></html>"""

# -- Admin Page --
@flask_app.route("/admin", methods=["GET", "POST"])
@min_level(50)
def admin_page():
    msg = None
    user_level = get_user_level(session["username"])
    is_ceo = user_level >= 100

    if request.method == "POST":
        action = request.form.get("action", "")
        data = load_accounts()

        if action == "add" and user_level >= 50:
            new_user = request.form.get("new_username", "").lower().strip()
            new_pw   = request.form.get("new_password", "")
            new_role = request.form.get("new_role", "staff")
            if new_user and new_pw and new_user not in data["users"]:
                if new_role not in data["roles"]:
                    new_role = "staff"
                data["users"][new_user] = {
                    "password_hash": hash_password(new_pw),
                    "role": new_role,
                    "discord_id": None
                }
                save_accounts(data)
                msg = ("ok", f"Account '{new_user}' created.")
            else:
                msg = ("error", "Invalid username or username already exists.")

        elif action == "remove" and user_level >= 50:
            target = request.form.get("remove_user", "").lower().strip()
            if target == "k.north":
                msg = ("error", "Cannot remove the CEO account.")
            else:
                target_level = get_user_level(target, data)
                if user_level <= target_level:
                    msg = ("error", "Cannot remove a user with equal or higher clearance.")
                elif target in data["users"]:
                    del data["users"][target]
                    save_accounts(data)
                    msg = ("ok", f"Account '{target}' removed.")
                else:
                    msg = ("error", "User not found.")

        elif action == "change_role" and user_level >= 50:
            target = request.form.get("role_user", "").lower().strip()
            new_role = request.form.get("role_value", "staff")
            if target == "k.north":
                msg = ("error", "Cannot change the CEO role.")
            elif new_role not in data["roles"]:
                msg = ("error", "Invalid role.")
            elif target in data["users"]:
                target_level = get_user_level(target, data)
                if user_level <= target_level and not is_ceo:
                    msg = ("error", "Cannot change role of a user with equal or higher clearance.")
                else:
                    data["users"][target]["role"] = new_role
                    save_accounts(data)
                    msg = ("ok", f"Role for '{target}' set to '{new_role}'.")
            else:
                msg = ("error", "User not found.")

        # --- CEO-only actions ---
        elif action == "add_role" and is_ceo:
            role_name = request.form.get("role_name", "").lower().strip()
            role_label = request.form.get("role_label", "").strip()
            role_level = int(request.form.get("role_level", "10"))
            if role_name and role_label and role_name not in data["roles"]:
                if role_name == "ceo":
                    msg = ("error", "Cannot create a role named 'ceo'.")
                elif role_level >= 100:
                    msg = ("error", "Cannot create a role at or above CEO level.")
                else:
                    data["roles"][role_name] = {"level": role_level, "label": role_label}
                    save_accounts(data)
                    msg = ("ok", f"Role '{role_label}' created.")
            else:
                msg = ("error", "Invalid or duplicate role name.")

        elif action == "remove_role" and is_ceo:
            role_name = request.form.get("remove_role", "").lower().strip()
            if role_name in ("ceo", "staff"):
                msg = ("error", "Cannot remove the CEO or Staff roles.")
            elif role_name in data["roles"]:
                # Reassign any users with this role to staff
                for uname, uinfo in data["users"].items():
                    if uinfo.get("role") == role_name:
                        uinfo["role"] = "staff"
                del data["roles"][role_name]
                save_accounts(data)
                msg = ("ok", f"Role '{role_name}' removed. Affected users reassigned to Staff.")
            else:
                msg = ("error", "Role not found.")

    data = load_accounts()
    role_label = get_user_role_label(session["username"], data)

    # Users table
    users_html = ""
    for name, info in data["users"].items():
        ur = info.get("role", "staff")
        rl = data["roles"].get(ur, {}).get("label", ur)
        users_html += f"<tr><td>{name}</td><td>{rl}</td><td>{info.get('discord_id','—')}</td></tr>"

    # Role select options
    role_options = "".join(
        f"<option value='{rn}'>{ri['label']} (Level {ri['level']})</option>"
        for rn, ri in data["roles"].items()
    )

    # Roles table
    roles_html = ""
    for rn, ri in data["roles"].items():
        roles_html += f"<tr><td>{ri['label']}</td><td>{rn}</td><td>{ri['level']}</td></tr>"

    # Backup JSON
    backup_json = get_backup_json()

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin — NORAI Trade Calculator</title>{BASE_STYLE}
</head><body>
<div class="container">
<div class="user-bar">Logged in as: <strong style="color:#c45a1a;">{session['username']}</strong> [{role_label}]</div>
<div class="nav">
  <a href="/calculator">Calculator</a>
  <a href="/admin" class="active">Admin</a>
  <a href="/settings">Settings</a>
  <a href="/logout" class="logout">Logout</a>
</div>
<h1>Account Management</h1>
{"<p class='flash flash-" + msg[0] + "'>" + msg[1] + "</p>" if msg else ""}

<h2>Registered Users</h2>
<table>
<thead><tr><th>Username</th><th>Role</th><th>Discord ID</th></tr></thead>
<tbody>{users_html}</tbody>
</table>

<h2>Add User</h2>
<form method="POST">
<input type="hidden" name="action" value="add">
<label>Username</label><input type="text" name="new_username" required>
<label>Password</label><input type="password" name="new_password" required>
<label>Role</label><select name="new_role">{role_options}</select>
<button type="submit" class="btn">Create Account</button>
</form>

<h2>Remove User</h2>
<form method="POST">
<input type="hidden" name="action" value="remove">
<label>Username</label><input type="text" name="remove_user" required>
<button type="submit" class="btn btn-danger btn-sm">Remove</button>
</form>

<h2>Change Role</h2>
<form method="POST">
<input type="hidden" name="action" value="change_role">
<label>Username</label><input type="text" name="role_user" required>
<label>New Role</label><select name="role_value">{role_options}</select>
<button type="submit" class="btn">Change Role</button>
</form>

{"<!-- ===== CEO-ONLY SECTION ===== -->" if is_ceo else ""}
{"<hr style='border-color:#1a1a1a;margin:40px 0;'>" if is_ceo else ""}
{"<h2 style='color:#c45a1a;'>Role Management [CEO]</h2>" if is_ceo else ""}

{"<h3>Current Roles</h3><table><thead><tr><th>Label</th><th>Name</th><th>Level</th></tr></thead><tbody>" + roles_html + "</tbody></table>" if is_ceo else ""}

{"<h3>Add New Role</h3><form method='POST'><input type='hidden' name='action' value='add_role'><label>Role Name (internal, lowercase)</label><input type='text' name='role_name' required><label>Display Label</label><input type='text' name='role_label' required><label>Clearance Level (1-99)</label><input type='number' name='role_level' value='30' min='1' max='99'><button type='submit' class='btn'>Create Role</button></form>" if is_ceo else ""}

{"<h3>Remove Role</h3><form method='POST'><input type='hidden' name='action' value='remove_role'><label>Role Name</label><input type='text' name='remove_role' required><button type='submit' class='btn btn-danger btn-sm'>Remove Role</button></form><p style='color:#666;font-size:11px;'>Users with this role will be reassigned to Staff.</p>" if is_ceo else ""}

{"<h3>Export Backup</h3><p style='color:#888;'>Copy this JSON and paste it into the <code>ACCOUNTS_BACKUP</code> environment variable in Render. This protects against data loss on service restart.</p><div class='backup-box' id='backup-json'>" + backup_json + "</div><button class='btn btn-sm' onclick='copyBackup()' style='margin-top:8px;'>Copy to Clipboard</button>" if is_ceo else ""}

</div>
{"<script>function copyBackup() {{ var text = document.getElementById('backup-json').innerText; navigator.clipboard.writeText(text).then(function() {{ alert('Backup copied to clipboard.'); }}); }}</script>" if is_ceo else ""}
</body></html>"""

# -- Health --
@flask_app.route("/health")
def health():
    return "NORAI operational."

# -- Inara Proxy --
INARA_API_URL = "https://inara.cz/inapi/v1/"

@flask_app.route("/api/inara-proxy", methods=["POST"])
@login_required
def inara_proxy():
    data = request.get_json()
    api_key = data.get("api_key", "").strip()
    commodity = data.get("commodity", "").strip()
    if not api_key or not commodity:
        return jsonify({"error": "API key and commodity are required."})
    payload = {
        "header": {
            "appName": "NORAI Trade Calculator",
            "appVersion": "1.0",
            "APIkey": api_key
        },
        "events": [{
            "eventName": "getCommodityMarket",
            "eventTimestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "eventData": {"commodityName": commodity}
        }]
    }
    try:
        resp = requests.post(INARA_API_URL, json=payload, timeout=15)
        result = resp.json()
        listings = []
        events = result.get("events", [])
        if events:
            for entry in events[0].get("eventData", []):
                listings.append({
                    "station": entry.get("stationName", "Unknown"),
                    "system": entry.get("starsystemName", "Unknown"),
                    "price": entry.get("commoditySellPrice") or entry.get("commodityBuyPrice") or 0,
                    "is_sell": entry.get("commoditySellPrice") is not None,
                    "supply": entry.get("commodityStock", 0),
                    "updated": entry.get("updateTime", "N/A")
                })
        listings.sort(key=lambda x: (not x["is_sell"], -x["price"] if x["is_sell"] else x["price"]))
        return jsonify({"listings": listings[:20]})
    except requests.RequestException as e:
        logger.error(f"Inara proxy error: {e}")
        return jsonify({"error": "Could not reach Inara API. Try again later."})
    except Exception as e:
        logger.error(f"Inara proxy parse error: {e}")
        return jsonify({"error": "Unexpected response from Inara. Check your API key."})

# ============================================================
# DISCORD BOT
# ============================================================

WELCOME_MESSAGE = """**NORTHCORP [NINC] — INCOMING TRANSMISSION**

*Automated dispatch from NORAI, the NORTHCORP Administrative Intelligence.*

Welcome to the NORTHCORP communications network. You have been granted guest access to our public channels.

**What you can do here:**
• Browse the **Contract Board** for open haulage and supply contracts.
• Monitor **Company Bulletins** for fleet movements and corporate notices.
• Hail us on **Open Hailing** frequencies with contract inquiries.

**Next step:**
• Introduce yourself in <#{arrivals_channel}> — state your ship and business.

CEO Kalvin North reviews all inquiries personally. Response within one standard business cycle.

*Fly safe. Deliver on time. That is the NORTHCORP way.*"""

STATUS_RESPONSE = """**NORAI System Status** — {timestamp}

```
Core Systems:       NOMINAL
Communications:     ONLINE
Fleet Assets:       {fleet_summary}
Contract Queue:     {contract_count} PENDING
CEO Status:         {ceo_status}
```

All systems operational. Awaiting directives."""

ABOUT_RESPONSE = """**NORTHCORP [NINC]** — Trading & Mining Contractor

**Founded:** 3310
**CEO:** Kalvin North
**Operational HQ:** Czerny Landing, Kurukana System
**Services:** Commodity haulage · Resource extraction · Carrier logistics

NORTHCORP is a privately held contractor serving established organizations across the Core Systems. We handle bulk transport, mining supply contracts, and fleet carrier loading/unloading operations.

*"No job too large or too remote."*

For contract inquiries, use **/contact**."""

SERVICES_RESPONSE = """**NORTHCORP — Services Offered**

```
[01] COMMODITY HAULAGE
     Bulk transport. Source-to-destination.
     Carrier loading and unloading.

[02] RESOURCE EXTRACTION
     Mining contracts. Laser, core, sub-surface.
     Raw and refined mineral supply.

[03] CARRIER LOGISTICS
     Loading/unloading coordination.
     Jump scheduling. Cargo transfer management.
```

All contracts negotiated directly with CEO North. Competitive rates. Verified delivery.

Use **/contact** to open a negotiation."""

CONTACT_RESPONSE = """**NORTHCORP — Contact CEO North**

All business communications are handled directly by CEO Kalvin North. No intermediaries.

• **Discord:** Direct message or use the Open Hailing channel
• **Website:** NORTHCORP corporate page
• **Trade Calculator:** NORAI Trade Calculator (employee access)
• **HQ:** Czerny Landing, Kurukana — in-person meetings by appointment only

Response within one standard business cycle (24 hours).

*This is not an automated call center. You will speak to the man himself.*"""

def fetch_fleet():
    try:
        resp = requests.get(FLEET_JSON_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Fleet fetch failed: {e}")
        return None

def in_game_datetime():
    now = datetime.utcnow()
    ig_year = now.year + 1286
    return now.strftime(f"%Y-%m-%d %H:%M UTC").replace(
        str(now.year), str(ig_year), 1
    )

def fmt_cr(n):
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B CR"
    elif n >= 1_000_000:
        return f"{n/1_000_000:.1f}M CR"
    elif n >= 1_000:
        return f"{n/1_000:.0f}K CR"
    return f"{n:.0f} CR"

class NORAIBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        logger.info("Setup complete. Skipping command sync to avoid rate limits.")

bot = NORAIBot()

@bot.event
async def on_ready():
    logger.info(f"NORAI online — {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guild(s).")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="NORTHCORP comms | /help"
        )
    )

@bot.event
async def on_member_join(member: discord.Member):
    arrivals = f"<#{CHANNEL_ARRIVALS}>" if CHANNEL_ARRIVALS else "#arrivals-dock"
    welcome = WELCOME_MESSAGE.format(arrivals_channel=arrivals)
    try:
        await member.send(welcome)
        logger.info(f"Welcome DM sent to {member.name}.")
    except discord.Forbidden:
        logger.warning(f"Could not DM {member.name} — DMs closed.")

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)
    content = message.content.lower().strip()
    if content in ("hello norai", "norai", "norai, hello"):
        await message.channel.send(
            f"On station, {message.author.display_name}. "
            "I am NORAI, the NORTHCORP Administrative Intelligence. "
            "Use **/help** for available commands."
        )
    elif content in ("norai report", "norai, report", "report norai"):
        fleet = fetch_fleet()
        if fleet:
            active = sum(1 for s in fleet if s.get("status", "").upper() == "ACTIVE")
            fleet_line = f"{active}/{len(fleet)} operational"
        else:
            fleet_line = "data unavailable"
        await message.channel.send(
            f"NORAI reporting. Fleet: {fleet_line}. "
            f"All systems nominal. CEO status: {ceo_status}. "
            f"Use **/contact** for contract inquiries."
        )
    elif "norai" in content and "thank" in content:
        await message.channel.send(
            "Acknowledged. Forward your gratitude to CEO North — "
            "he built this operation from the ground up."
        )

@bot.command(name="sync")
async def cmd_sync(ctx: commands.Context):
    if ctx.author.id != CEO_DISCORD_ID:
        await ctx.send("Access denied.")
        return
    await ctx.send("Syncing commands...")
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    await ctx.send("Commands synced.")

@bot.tree.command(name="status", description="NORAI system status and current operations.")
async def cmd_status(interaction: discord.Interaction):
    fleet = fetch_fleet()
    if fleet:
        active = sum(1 for s in fleet if s.get("status", "").upper() == "ACTIVE")
        fleet_summary = f"{active}/{len(fleet)} operational"
    else:
        fleet_summary = "data unavailable"
    await interaction.response.send_message(
        STATUS_RESPONSE.format(
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            fleet_summary=fleet_summary,
            contract_count="0",
            ceo_status=ceo_status,
        )
    )

@bot.tree.command(name="fleet", description="View the NORTHCORP active fleet registry.")
async def cmd_fleet(interaction: discord.Interaction):
    fleet = fetch_fleet()
    if not fleet:
        await interaction.response.send_message(
            "Fleet data unavailable. Consult the corporate website for current registry.",
            ephemeral=True
        )
        return
    lines = ["**NORTHCORP — Active Fleet Registry**\n"]
    for ship in fleet:
        lines.append(
            f"- **{ship['name']}** [{ship['identifier']}] — {ship['class']} — "
            f"{ship['role']} — Value: {ship['value']} — **{ship['status']}**"
        )
    lines.append(
        "\nFleet carrier operational. Inquiries regarding carrier loading "
        "contracts welcome. Use **/contact**."
    )
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="about", description="About NORTHCORP — who we are and what we do.")
async def cmd_about(interaction: discord.Interaction):
    await interaction.response.send_message(ABOUT_RESPONSE)

@bot.tree.command(name="services", description="List NORTHCORP services.")
async def cmd_services(interaction: discord.Interaction):
    await interaction.response.send_message(SERVICES_RESPONSE)

@bot.tree.command(name="contact", description="How to reach CEO Kalvin North for contracts.")
async def cmd_contact(interaction: discord.Interaction):
    await interaction.response.send_message(CONTACT_RESPONSE)

@bot.tree.command(name="help", description="List all available NORAI commands.")
async def cmd_help(interaction: discord.Interaction):
    help_text = """**NORAI — Available Commands**

```
/status    — System status and CEO location
/fleet     — Fleet registry (ships, roles, status)
/about     — About NORTHCORP
/services  — Services offered
/contact   — Contact CEO North
/clock     — Current in-game date and time
/quote     — Random NORTHCORP motto
/trade     — Quick trade profit calculator
/log       — Submit a flight log (Contractor+)
/help      — This list
```

I also respond to:
• "NORAI" or "hello NORAI"
• "NORAI, report"

**NORAI Trade Calculator (web):** Employee login required.
For detailed trade planning with Inara integration, visit the employee portal."""
    await interaction.response.send_message(help_text)

@bot.tree.command(name="clock", description="Display current in-game date and time (3310+).")
async def cmd_clock(interaction: discord.Interaction):
    ig_time = in_game_datetime()
    utc_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await interaction.response.send_message(
        f"**Galactic Standard Time**\n\n"
        f"- **In-Game:** {ig_time}\n"
        f"- **UTC:** {utc_time}\n\n"
        f"*NORTHCORP operates on 24-hour galactic standard. "
        f"All contract deadlines use GST.*"
    )

@bot.tree.command(name="quote", description="Random NORTHCORP motto or Kalvin-ism.")
async def cmd_quote(interaction: discord.Interaction):
    await interaction.response.send_message(f"*{random.choice(QUOTES)}*")

@bot.tree.command(name="trade", description="Quick trade profit calculator (manual entry).")
@app_commands.describe(
    commodity="Commodity name (e.g., Gold)",
    buy_price="Buy price per ton (CR)",
    sell_price="Sell price per ton (CR)",
    tonnage="Tonnage to haul",
    commission="Commission per ton in CR (e.g., 5000)"
)
async def cmd_trade(
    interaction: discord.Interaction,
    commodity: str,
    buy_price: float,
    sell_price: float,
    tonnage: float,
    commission: float = 0.0
):
    if buy_price <= 0 or sell_price <= 0 or tonnage <= 0:
        await interaction.response.send_message(
            "All values must be positive numbers.",
            ephemeral=True
        )
        return

    buy_cost = buy_price * tonnage
    sell_rev = sell_price * tonnage
    gross = sell_rev - buy_cost
    comm_amt = commission * tonnage
    net = gross - comm_amt
    ppt = net / tonnage

    result = f"""**Trade Calculation — {commodity}**

```
Buy Price:    {buy_price:,.0f} CR/ton
Sell Price:   {sell_price:,.0f} CR/ton
Tonnage:      {tonnage:,.0f} t

Buy Cost:     {fmt_cr(buy_cost)}
Sell Revenue: {fmt_cr(sell_rev)}
Gross Margin: {fmt_cr(gross)}
Comm ({commission:,.0f} CR/t):  {fmt_cr(comm_amt)}
---
Net Profit:   {fmt_cr(net)}
Profit/Ton:   {fmt_cr(ppt)}
```

*For detailed trade planning with Inara price lookup, use the [NORAI Trade Calculator](https://northcorp-norai.onrender.com/calculator).*"""

    await interaction.response.send_message(result)

@bot.tree.command(name="log", description="[Contractor+] Submit a structured flight log.")
@app_commands.describe(
    ship="Ship used for this run (e.g., Atlas)",
    cargo="Cargo type and tonnage (e.g., 720t Gold)",
    origin="Origin system/station",
    destination="Destination system/station",
    profit="Profit earned (e.g., 12,400,000 CR)",
    notes="Any additional notes (optional)"
)
async def cmd_log(
    interaction: discord.Interaction,
    ship: str,
    cargo: str,
    origin: str,
    destination: str,
    profit: str,
    notes: str = ""
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        await interaction.response.send_message("Unable to verify permissions.", ephemeral=True)
        return
    role_names = [r.name for r in member.roles]
    if "CEO" not in role_names and "Contractor" not in role_names:
        await interaction.response.send_message(
            "Access denied. Flight logs are restricted to NORTHCORP personnel.",
            ephemeral=True
        )
        return
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    notes_str = notes if notes else "N/A"
    entry = (
        f"**FLIGHT LOG — {timestamp}**\n\n"
        f"```\n"
        f"Ship:        {ship}\n"
        f"Cargo:       {cargo}\n"
        f"Origin:      {origin}\n"
        f"Destination: {destination}\n"
        f"Profit:      {profit}\n"
        f"Notes:       {notes_str}\n"
        f"```\n\n"
        f"*Logged by {interaction.user.display_name} — NORTHCORP [NINC]*"
    )
    channel = bot.get_channel(CHANNEL_FLIGHT_LOGS)
    if channel is None:
        await interaction.response.send_message("Error: Flight log channel not found. Notify CEO North.", ephemeral=True)
        return
    if isinstance(channel, discord.ForumChannel):
        thread = await channel.create_thread(
            name=f"{ship} — {origin} to {destination} — {timestamp[:10]}",
            content=entry
        )
        await interaction.response.send_message(f"Flight log posted: {thread.thread.mention}", ephemeral=True)
    else:
        await channel.send(entry)
        await interaction.response.send_message("Flight log posted.", ephemeral=True)

@bot.tree.command(name="setstatus", description="[CEO] Set your current location or status.")
@app_commands.describe(status="Your current status (e.g., 'In transit to Lave', 'At HQ')")
async def cmd_setstatus(interaction: discord.Interaction, status: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message("Access denied. CEO credentials required.", ephemeral=True)
        return
    global ceo_status
    ceo_status = status
    await interaction.response.send_message(f"CEO status updated: **{status}**", ephemeral=True)
    logger.info(f"CEO status set to: {status}")

@bot.tree.command(name="broadcast", description="[CEO] Broadcast a message to Company Bulletins.")
@app_commands.describe(message="The message to broadcast publicly.")
async def cmd_broadcast(interaction: discord.Interaction, message: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message("Access denied. CEO credentials required.", ephemeral=True)
        return
    channel = bot.get_channel(CHANNEL_BULLETINS)
    if channel is None:
        await interaction.response.send_message("Error: Bulletins channel not found.", ephemeral=True)
        return
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await channel.send(f"**NORTHCORP BULLETIN** — {timestamp}\n\n{message}")
    await interaction.response.send_message("Bulletin transmitted.", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Standby. Command on cooldown — retry in {error.retry_after:.0f}s.",
            ephemeral=True
        )
    else:
        logger.error(f"Command error: {error}")
        await interaction.response.send_message("Internal error. Notify CEO North.", ephemeral=True)

# ============================================================
# STARTUP
# ============================================================

def start_flask():
    ensure_defaults()
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN not found in .env file.")
        exit(1)

    threading.Thread(target=start_flask, daemon=True).start()
    logger.info(f"Flask started on port {PORT}.")

    bot.run(TOKEN)
