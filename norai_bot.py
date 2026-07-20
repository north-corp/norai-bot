import os
import json
import random
import hashlib
import secrets
import logging
import threading
import time
from datetime import datetime, timezone
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
SPANSH_ROUTE_URL = "https://spansh.co.uk/api/trade/route"
SPANSH_RESULT_URL = "https://spansh.co.uk/api/results"

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
    for name, info in DEFAULT_ROLES.items():
        if name not in data["roles"]:
            data["roles"][name] = info
    if "k.north" not in data["users"]:
        data["users"]["k.north"] = {
            "password_hash": hash_password("tethlon"),
            "role": "ceo",
            "discord_id": str(CEO_DISCORD_ID)
        }
        logger.info("Created default CEO account (username: k.north). CHANGE THE PASSWORD.")
    else:
        data["users"]["k.north"]["role"] = "ceo"
    save_accounts(data)
    if ACCOUNTS_BACKUP:
        try:
            backup = json.loads(ACCOUNTS_BACKUP)
            backup_users = len(backup.get("users", {}))
            local_users = len(data["users"])
            if backup_users > local_users:
                logger.info(f"Restoring accounts from backup ({backup_users} users vs {local_users} local).")
                data = backup
                if "roles" not in data:
                    data["roles"] = dict(DEFAULT_ROLES)
                data["users"]["k.north"]["role"] = "ceo"
                save_accounts(data)
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
    return data

def get_backup_json():
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

BASE_STYLE = """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0a0a; color: #c0c0c0; font-family: "Courier New", Courier, monospace; line-height: 1.6; font-size: 15px; }
  .container { max-width: 960px; margin: 0 auto; padding: 40px 24px; }
  h1 { color: #c45a1a; font-size: 28px; letter-spacing: 4px; margin-bottom: 20px; }
  h2 { color: #c45a1a; font-size: 18px; letter-spacing: 2px; margin: 30px 0 16px; }
  h3 { color: #e0e0e0; font-size: 15px; margin-bottom: 10px; }
  label { display: block; color: #888; font-size: 12px; letter-spacing: 1px; text-transform: uppercase; margin: 12px 0 4px; }
  input, select, textarea { background: #0f0f0f; color: #e0e0e0; border: 1px solid #1a1a1a; padding: 10px 14px; font-family: "Courier New", monospace; font-size: 14px; width: 100%; max-width: 500px; }
  input:focus, select:focus, textarea:focus { border-color: #c45a1a; outline: none; }
  .btn { display: inline-block; background: #c45a1a; color: #0a0a0a; font-weight: bold; font-size: 13px; letter-spacing: 2px; text-transform: uppercase; text-decoration: none; padding: 12px 28px; border: none; cursor: pointer; font-family: "Courier New", monospace; margin-top: 16px; }
  .btn:hover { background: #d9702a; }
  .btn-ghost { background: transparent; color: #c45a1a; border: 2px solid #c45a1a; font-family: "Courier New", monospace; font-weight: bold; font-size: 13px; letter-spacing: 2px; text-transform: uppercase; padding: 12px 28px; cursor: pointer; }
  .btn-ghost:hover { background: #1a1008; }
  .btn-sm { font-size: 11px; padding: 6px 14px; letter-spacing: 1px; }
  .btn-danger { background: #8b3030; color: #e0e0e0; border: none; }
  .btn-danger:hover { background: #a04040; }
  .result-box { background: #0f0f0f; border: 1px solid #1a1a1a; padding: 24px; margin-top: 20px; }
  .result-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
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
  .mode-tabs { display: flex; gap: 0; margin-bottom: 20px; flex-wrap: wrap; }
  .mode-tab { background: #0f0f0f; color: #888; border: 1px solid #1a1a1a; padding: 10px 20px; cursor: pointer; font-family: "Courier New", monospace; font-size: 13px; letter-spacing: 1px; text-transform: uppercase; }
  .mode-tab.active { background: #1a1008; color: #c45a1a; border-color: #c45a1a; }
  .inline { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .gap-row { display: flex; gap: 12px; flex-wrap: wrap; }
  .gap-row > * { flex: 1; min-width: 150px; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid #1a1a1a; font-size: 13px; }
  th { color: #666; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
  .hidden { display: none; }
  .backup-box { background: #0f0f0f; border: 1px solid #2a2a0a; padding: 16px; max-height: 200px; overflow-y: auto; font-size: 11px; color: #888; word-break: break-all; white-space: pre-wrap; margin-top: 8px; }
  .progress-bar { width: 100%; height: 4px; background: #1a1a1a; margin-top: 12px; }
  .progress-fill { height: 100%; background: #c45a1a; transition: width 0.3s; }
  .tag { display: inline-block; background: #1a1008; color: #c45a1a; border: 1px solid #3a2a10; padding: 3px 10px; font-size: 11px; margin: 2px; cursor: pointer; }
  .tag:hover { background: #2a1a0c; }
  .tag .remove { color: #a04040; margin-left: 6px; font-weight: bold; }
  @media (max-width: 600px) { .container { padding: 20px 16px; } .result-grid { grid-template-columns: 1fr; } .mode-tab { padding: 8px 12px; font-size: 11px; } }
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

# ============================================================
# CALCULATOR
# ============================================================

@flask_app.route("/calculator")
@login_required
def calculator_page():
    data = load_accounts()
    user = data["users"].get(session["username"], {})
    role_label = get_user_role_label(session["username"], data)
    user_level = get_user_level(session["username"], data)
    is_admin = user_level >= 50
    saved_favs = json.dumps(user.get("settings", {}).get("favourites", ["platinum", "gold", "tritium"]))
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
  <div class="mode-tab active" id="tab-pilot" onclick="switchMode('pilot')">Pilot</div>
  <div class="mode-tab" id="tab-carrier" onclick="switchMode('carrier')">Carrier Full Run</div>
  <div class="mode-tab" id="tab-scanner" onclick="switchMode('scanner')">Route Scanner</div>
  <div class="mode-tab" id="tab-reverse" onclick="switchMode('reverse')">Reverse</div>
</div>

<div id="mode-pilot">
<p style="color:#888;margin-bottom:16px;">Single pilot buying from a station and selling to a carrier or another station.</p>
<div class="gap-row">
  <div><label>Commodity</label><input type="text" id="p-commodity" placeholder="e.g. Gold"></div>
  <div><label>Buy Price at Station (CR/t)</label><input type="number" id="p-buy" placeholder="45000" step="1" min="0"></div>
</div>
<div class="gap-row">
  <div><label>Sell Price to Carrier (CR/t)</label><input type="number" id="p-sell" placeholder="60000" step="1" min="0"></div>
  <div><label>Tonnage</label><input type="number" id="p-tons" placeholder="720" step="1" min="0"></div>
</div>
<div class="gap-row">
  <div><label>Carrier Commission (CR/t)</label><input type="number" id="p-comm" placeholder="10000" step="1" min="0"></div>
</div>
<p style="color:#666;font-size:11px;">Carrier pays pilot: <strong>Buy Price + Commission</strong>. Pilot profits Commission x Tonnage.</p>
<button class="btn" onclick="calcPilot()">Calculate</button>
<div class="result-box hidden" id="pilot-result">
<h3>Results — <span id="p-commodity-out"></span></h3>
<div class="result-grid">
  <div class="result-item"><label>Pilot Pays Station</label><div class="value value-cost" id="p-station-cost"></div></div>
  <div class="result-item"><label>Carrier Pays Pilot</label><div class="value value-profit" id="p-carrier-pays"></div></div>
  <div class="result-item"><label>Pilot Net Profit</label><div class="value value-profit" id="p-net"></div></div>
  <div class="result-item"><label>Pilot Profit/Ton</label><div class="value" id="p-ppt"></div></div>
  <div class="result-item"><label>Carrier Cost/Ton</label><div class="value value-cost" id="p-carrier-cost-ton"></div></div>
</div>
</div>
</div>

<div id="mode-carrier" class="hidden">
<p style="color:#888;margin-bottom:16px;">Full PTN carrier operation. Both commissions must be at least 10,000 CR/t.</p>
<h2>Leg 1 — Loading</h2>
<div class="gap-row">
  <div><label>Station Buy Price (CR/t)</label><input type="number" id="c-buy" placeholder="45000" step="1" min="0"></div>
  <div><label>Tonnage</label><input type="number" id="c-tons" placeholder="25000" step="1" min="0"></div>
</div>
<div class="gap-row">
  <div><label>Loading Commission (CR/t)</label><input type="number" id="c-load-comm" placeholder="15000" step="1" min="10000"><span style="color:#666;font-size:11px;">Min 10,000</span></div>
</div>
<p style="color:#666;font-size:11px;">Carrier buy order = Station Buy Price + Loading Commission.</p>
<h2>Transit</h2>
<label>Tritium Fuel Cost (CR)</label><input type="number" id="c-trit" placeholder="0" step="1" min="0" value="0">
<h2>Leg 2 — Unloading</h2>
<div class="gap-row">
  <div><label>Station Sell Price (CR/t)</label><input type="number" id="c-sell" placeholder="85000" step="1" min="0"></div>
</div>
<div class="gap-row">
  <div><label>Unloading Commission (CR/t)</label><input type="number" id="c-unload-comm" placeholder="15000" step="1" min="10000"><span style="color:#666;font-size:11px;">Min 10,000</span></div>
</div>
<p style="color:#666;font-size:11px;">Carrier sell order = Station Sell Price - Unloading Commission.</p>
<button class="btn" onclick="calcCarrier()">Calculate</button>
<div class="result-box hidden" id="carrier-result">
<h3>Full Run Results</h3>
<div class="result-grid">
  <div class="result-item"><label>Carrier Buy Order Price</label><div class="value value-cost" id="c-buy-order"></div></div>
  <div class="result-item"><label>Carrier Sell Order Price</label><div class="value value-profit" id="c-sell-order"></div></div>
  <div class="result-item"><label>Total Loading Cost</label><div class="value value-cost" id="c-load-cost"></div></div>
  <div class="result-item"><label>Total Unloading Revenue</label><div class="value value-profit" id="c-unload-rev"></div></div>
  <div class="result-item"><label>Carrier Net Profit</label><div class="value value-profit" id="c-net"></div></div>
  <div class="result-item"><label>Profit per Ton</label><div class="value" id="c-ppt"></div></div>
</div>
<h3>Pilot Payouts</h3>
<div class="result-grid">
  <div class="result-item"><label>Loading Pilot Earns (per ton)</label><div class="value value-profit" id="c-load-ppt"></div></div>
  <div class="result-item"><label>Unloading Pilot Earns (per ton)</label><div class="value value-profit" id="c-unload-ppt"></div></div>
</div>
<div id="c-commission-warn" style="margin-top:12px;"></div>
</div>
</div>

<div id="mode-scanner" class="hidden">
<p style="color:#888;margin-bottom:16px;">Scans Spansh for profitable trade routes from your carrier's location. Finds the best volume-safe route from real market data.</p>
<h2>Carrier Location</h2>
<div class="gap-row">
  <div><label>Current System</label><input type="text" id="s-system" placeholder="e.g. Tethlon" value="Tethlon"></div>
  <div><label>Current Station</label><input type="text" id="s-station" placeholder="e.g. Mallory Orbital" value="Mallory Orbital"></div>
</div>
<h2>Favourites</h2>
<div id="fav-tags" style="margin-bottom:8px;"></div>
<div class="inline">
  <input type="text" id="fav-input" placeholder="Add commodity..." style="flex:1;max-width:300px;">
  <button class="btn btn-sm" onclick="addFav()" style="margin-top:0;">Add</button>
  <button class="btn btn-sm btn-ghost" onclick="saveFavs()" style="margin-top:0;">Save to Server</button>
</div>
<h2>Scan Parameters</h2>
<div class="gap-row">
  <div><label>Tonnage</label><input type="number" id="s-tons" placeholder="25000" step="1" min="0" value="25000"></div>
  <div><label>Safety Margin (t)</label><input type="number" id="s-safety" placeholder="5000" step="1" min="0" value="5000"></div>
</div>
<div class="gap-row">
  <div><label>Loading Commission (CR/t)</label><input type="number" id="s-load-comm" placeholder="15000" step="1" min="10000" value="15000"></div>
  <div><label>Unloading Commission (CR/t)</label><input type="number" id="s-unload-comm" placeholder="15000" step="1" min="10000" value="15000"></div>
</div>
<div class="gap-row">
  <div><label>Min Carrier Profit (CR total)</label><input type="number" id="s-min-profit" placeholder="250000000" step="1" min="0" value="250000000"></div>
  <div><label>Max Route Distance (LY)</label><input type="number" id="s-max-dist" placeholder="50000" step="1" min="0" value="50000"></div>
</div>
<button class="btn" onclick="runScanner()">Scan All Favourites</button>
<div id="scanner-progress" class="hidden" style="margin-top:16px;">
  <p id="scanner-status" style="color:#888;"></p>
  <div class="progress-bar"><div class="progress-fill" id="scanner-bar" style="width:0%;"></div></div>
</div>
<div id="scanner-results"></div>
</div>

<div id="mode-reverse" class="hidden">
<p style="color:#888;margin-bottom:16px;">Work backwards: given the station buy price and commissions, find the minimum station sell price you need.</p>
<h2>Your Parameters</h2>
<div class="gap-row">
  <div><label>Station Buy Price (CR/t)</label><input type="number" id="r-buy" placeholder="45000" step="1" min="0"></div>
  <div><label>Tonnage</label><input type="number" id="r-tons" placeholder="25000" step="1" min="0"></div>
</div>
<div class="gap-row">
  <div><label>Loading Commission (CR/t)</label><input type="number" id="r-load-comm" placeholder="15000" step="1" min="10000"></div>
  <div><label>Unloading Commission (CR/t)</label><input type="number" id="r-unload-comm" placeholder="15000" step="1" min="10000"></div>
</div>
<div class="gap-row">
  <div><label>Your Minimum Profit (CR/t)</label><input type="number" id="r-min-ppt" placeholder="10000" step="1" min="0"></div>
</div>
<button class="btn" onclick="calcReverse()">Calculate Required Sell Price</button>
<div class="result-box hidden" id="reverse-result">
<h3>What You Need</h3>
<div class="result-grid">
  <div class="result-item"><label>Required Station Sell Price</label><div class="value value-profit" id="r-req-sell"></div></div>
  <div class="result-item"><label>Required Spread</label><div class="value value-neutral" id="r-spread"></div></div>
  <div class="result-item"><label>Carrier Buy Order Price</label><div class="value value-cost" id="r-buy-order"></div></div>
  <div class="result-item"><label>Carrier Sell Order Price</label><div class="value value-profit" id="r-sell-order"></div></div>
  <div class="result-item"><label>Your Total Profit</label><div class="value value-profit" id="r-total-profit"></div></div>
  <div class="result-item"><label>ROI %</label><div class="value" id="r-roi"></div></div>
</div>
</div>
</div>

</div>

<script>
var favs = {saved_favs};

function switchMode(m) {{
  ['pilot','carrier','scanner','reverse'].forEach(function(x) {{
    document.getElementById('tab-'+x).classList.toggle('active', x===m);
    document.getElementById('mode-'+x).classList.toggle('hidden', x!==m);
  }});
}}

function fmt(n) {{ return n.toLocaleString('en-US') + ' CR'; }}
function fmtShort(n) {{
  if (Math.abs(n) >= 1e9) return (n/1e9).toFixed(2) + 'B CR';
  if (Math.abs(n) >= 1e6) return (n/1e6).toFixed(1) + 'M CR';
  if (Math.abs(n) >= 1e3) return (n/1e3).toFixed(0) + 'K CR';
  return n.toLocaleString('en-US') + ' CR';
}}

window.onload = function() {{ renderFavs(); }};

function renderFavs() {{
  var html = '';
  favs.forEach(function(f, i) {{
    html += '<span class="tag">' + f + '<span class="remove" onclick="removeFav('+i+')">&times;</span></span>';
  }});
  document.getElementById('fav-tags').innerHTML = html || '<span style="color:#666;">No favourites yet.</span>';
}}
function addFav() {{
  var inp = document.getElementById('fav-input');
  var val = inp.value.trim().toLowerCase();
  if (val && favs.indexOf(val) === -1) {{ favs.push(val); renderFavs(); }}
  inp.value = '';
}}
function removeFav(i) {{ favs.splice(i,1); renderFavs(); }}
async function saveFavs() {{
  try {{
    var resp = await fetch('/save_favourites', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{favourites: favs}}) }});
    if ((await resp.json()).status === 'ok') alert('Saved.');
  }} catch(e) {{ alert('Error.'); }}
}}

function calcPilot() {{
  var buy = parseFloat(document.getElementById('p-buy').value) || 0;
  var sell = parseFloat(document.getElementById('p-sell').value) || 0;
  var tons = parseFloat(document.getElementById('p-tons').value) || 0;
  var comm = parseFloat(document.getElementById('p-comm').value) || 0;
  var n = document.getElementById('p-commodity').value || 'Commodity';
  var sc = buy * tons, cp = sell * tons, pn = cp - sc;
  document.getElementById('p-commodity-out').textContent = n;
  document.getElementById('p-station-cost').textContent = fmt(Math.round(sc));
  document.getElementById('p-carrier-pays').textContent = fmt(Math.round(cp));
  document.getElementById('p-net').textContent = fmt(Math.round(pn));
  document.getElementById('p-ppt').textContent = fmt(Math.round(tons>0?pn/tons:0));
  document.getElementById('p-carrier-cost-ton').textContent = fmt(Math.round(sell));
  document.getElementById('pilot-result').classList.remove('hidden');
}}

function calcCarrier() {{
  var buy = parseFloat(document.getElementById('c-buy').value) || 0;
  var tons = parseFloat(document.getElementById('c-tons').value) || 0;
  var lc = parseFloat(document.getElementById('c-load-comm').value) || 0;
  var tr = parseFloat(document.getElementById('c-trit').value) || 0;
  var sell = parseFloat(document.getElementById('c-sell').value) || 0;
  var uc = parseFloat(document.getElementById('c-unload-comm').value) || 0;
  var bo = buy + lc, so = sell - uc;
  var lcost = bo * tons, urev = so * tons, net = urev - lcost - tr, ppt = tons>0?net/tons:0;
  document.getElementById('c-buy-order').textContent = fmt(Math.round(bo)) + ' /t';
  document.getElementById('c-sell-order').textContent = fmt(Math.round(so)) + ' /t';
  document.getElementById('c-load-cost').textContent = fmtShort(Math.round(lcost));
  document.getElementById('c-unload-rev').textContent = fmtShort(Math.round(urev));
  document.getElementById('c-net').textContent = fmtShort(Math.round(net));
  document.getElementById('c-ppt').textContent = fmt(Math.round(ppt));
  document.getElementById('c-load-ppt').textContent = fmt(Math.round(lc));
  document.getElementById('c-unload-ppt').textContent = fmt(Math.round(uc));
  var w = '';
  if (lc < 10000) w += '<p class="flash flash-error">Loading commission below 10,000 CR/t.</p>';
  if (uc < 10000) w += '<p class="flash flash-error">Unloading commission below 10,000 CR/t.</p>';
  if (ppt < 0) w += '<p class="flash flash-error">NET LOSS</p>';
  document.getElementById('c-commission-warn').innerHTML = w;
  document.getElementById('carrier-result').classList.remove('hidden');
}}

function calcReverse() {{
  var buy = parseFloat(document.getElementById('r-buy').value) || 0;
  var tons = parseFloat(document.getElementById('r-tons').value) || 0;
  var lc = parseFloat(document.getElementById('r-load-comm').value) || 0;
  var uc = parseFloat(document.getElementById('r-unload-comm').value) || 0;
  var mp = parseFloat(document.getElementById('r-min-ppt').value) || 0;
  var rs = buy + lc + uc + mp;
  document.getElementById('r-req-sell').textContent = fmt(Math.round(rs)) + ' /t';
  document.getElementById('r-spread').textContent = fmt(Math.round(rs - buy)) + ' /t';
  document.getElementById('r-buy-order').textContent = fmt(Math.round(buy + lc)) + ' /t';
  document.getElementById('r-sell-order').textContent = fmt(Math.round(rs - uc)) + ' /t';
  document.getElementById('r-total-profit').textContent = fmtShort(Math.round(mp * tons));
  document.getElementById('r-roi').textContent = ((buy+lc)>0 ? (mp/(buy+lc)*100).toFixed(1) : '0.0') + '%';
  document.getElementById('reverse-result').classList.remove('hidden');
}}

async function runScanner() {{
  var sys = document.getElementById('s-system').value.trim();
  var stn = document.getElementById('s-station').value.trim();
  if (!sys || !stn) {{ alert('Enter your carrier system and station.'); return; }}
  if (favs.length === 0) {{ alert('Add at least one commodity.'); return; }}

  var params = {{
    system: sys,
    station: stn,
    commodities: favs,
    tonnage: parseFloat(document.getElementById('s-tons').value) || 25000,
    safety_margin: parseFloat(document.getElementById('s-safety').value) || 5000,
    commission_load: parseFloat(document.getElementById('s-load-comm').value) || 15000,
    commission_unload: parseFloat(document.getElementById('s-unload-comm').value) || 15000,
    min_profit_total: parseFloat(document.getElementById('s-min-profit').value) || 250000000,
    max_distance: parseFloat(document.getElementById('s-max-dist').value) || 50000
  }};

  var resultDiv = document.getElementById('scanner-results');
  var progressDiv = document.getElementById('scanner-progress');
  var statusEl = document.getElementById('scanner-status');
  var barEl = document.getElementById('scanner-bar');

  resultDiv.innerHTML = '';
  progressDiv.classList.remove('hidden');
  statusEl.textContent = 'Submitting ' + favs.length + ' route requests to Spansh...';
  barEl.style.width = '5%';

  try {{
    var resp = await fetch('/api/scan-commodity', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(params)
    }});
    var data = await resp.json();
  }} catch(e) {{
    resultDiv.innerHTML = '<div class="result-box"><p class="flash flash-error">Network error.</p></div>';
    progressDiv.classList.add('hidden');
    return;
  }}

  if (data.error) {{
    resultDiv.innerHTML = '<div class="result-box"><p class="flash flash-error">' + data.error + '</p></div>';
    progressDiv.classList.add('hidden');
    return;
  }}

  var scannedOk = 0;
  if (data.per_commodity) {{
    scannedOk = data.per_commodity.filter(function(pc) {{ return pc.status === 'ok'; }}).length;
  }}
  statusEl.textContent = 'Scan complete. ' + data.total_trades + ' trades found. ' + scannedOk + '/' + (data.per_commodity ? data.per_commodity.length : favs.length) + ' commodities returned routes.';
  barEl.style.width = '100%';

  if (!data.trades || data.trades.length === 0) {{
    var html = '<div class="result-box"><p style="color:#a04040;">No viable trades found from ' + sys + ' / ' + stn + '.</p>';
    html += '<p style="color:#666;margin-top:8px;">Try different commodities, a larger max distance, or a different reference station.</p>';

    if (data.per_commodity) {{
      html += '<h3 style="margin-top:16px;">Per-Commodity Results</h3>';
      html += '<table><thead><tr><th>Commodity</th><th>Status</th><th>Routes</th><th>Passing</th><th>Error</th></tr></thead><tbody>';
      data.per_commodity.forEach(function(pc) {{
        html += '<tr>';
        html += '<td style="color:#c45a1a;">' + pc.commodity + '</td>';
        html += '<td style="color:' + (pc.status === 'ok' ? '#6a9a5b' : '#a04040') + ';">' + pc.status + '</td>';
        html += '<td>' + pc.routes_found + '</td>';
        html += '<td>' + pc.trades_passing_filters + '</td>';
        html += '<td style="color:#888;font-size:11px;">' + (pc.error || '—') + '</td>';
        html += '</tr>';
      }});
      html += '</tbody></table>';
    }}

    html += '</div>';
    resultDiv.innerHTML = html;
    return;
  }}

  data.trades.sort(function(a,b) {{ return b.carrier_total_profit - a.carrier_total_profit; }});

  var html = '<h2 style="margin-top:24px;">Best Trade</h2>';
  var t = data.trades[0];
  html += '<div class="result-box">';
  html += '<h3>' + t.commodity.toUpperCase() + '</h3>';
  html += '<div class="result-grid">';
  html += '<div class="result-item"><label>Buy at</label><div class="value" style="font-size:14px;">' + t.source_station + '</div><div style="color:#666;font-size:11px;">' + t.source_system + ' | ' + t.buy_price.toLocaleString() + ' CR/t</div></div>';
  html += '<div class="result-item"><label>Sell at</label><div class="value" style="font-size:14px;">' + t.dest_station + '</div><div style="color:#666;font-size:11px;">' + t.dest_system + ' | ' + t.sell_price.toLocaleString() + ' CR/t</div></div>';
  html += '<div class="result-item"><label>Volume</label><div class="value">' + t.available_amount.toLocaleString() + ' t</div></div>';
  html += '<div class="result-item"><label>Distance</label><div class="value">' + t.distance_ly.toFixed(0) + ' LY</div></div>';
  html += '<div class="result-item"><label>Spread</label><div class="value value-neutral">' + (t.sell_price - t.buy_price).toLocaleString() + ' CR/t</div></div>';
  html += '<div class="result-item"><label>Carrier Profit</label><div class="value value-profit">' + t.carrier_total_profit.toLocaleString() + ' CR</div><div style="color:#666;font-size:11px;">' + t.carrier_profit_per_ton.toLocaleString() + ' CR/t</div></div>';
  html += '</div>';
  html += '<p style="margin-top:16px;color:#888;">';
  html += '<strong>Carrier Buy Order:</strong> ' + (t.buy_price + params.commission_load).toLocaleString() + ' CR/t | ';
  html += '<strong>Carrier Sell Order:</strong> ' + (t.sell_price - params.commission_unload).toLocaleString() + ' CR/t';
  html += '</p></div>';

  if (data.trades.length > 1) {{
    html += '<h3>All Viable Trades (' + data.trades.length + ')</h3>';
    html += '<table><thead><tr><th>Comm</th><th>Buy</th><th>Sell</th><th>Vol</th><th>P/t</th><th>Total</th></tr></thead><tbody>';
    data.trades.forEach(function(r) {{
      html += '<tr>';
      html += '<td style="color:#c45a1a;">' + r.commodity + '</td>';
      html += '<td>' + r.source_station + ' ' + r.buy_price.toLocaleString() + '</td>';
      html += '<td>' + r.dest_station + ' ' + r.sell_price.toLocaleString() + '</td>';
      html += '<td>' + r.available_amount.toLocaleString() + '</td>';
      html += '<td style="color:#6a9a5b;">' + r.carrier_profit_per_ton.toLocaleString() + '</td>';
      html += '<td style="color:#6a9a5b;">' + r.carrier_total_profit.toLocaleString() + '</td>';
      html += '</tr>';
    }});
    html += '</tbody></table>';
  }}
  resultDiv.innerHTML = html;
}}
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
        pw = request.form.get("current_password", "")
        np = request.form.get("new_password", "")
        if user and check_password(pw, user["password_hash"]):
            user["password_hash"] = hash_password(np)
            save_accounts(data)
            msg = ("ok", "Password changed.")
        else:
            msg = ("error", "Current password incorrect.")
    rl = get_user_role_label(session["username"])
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Settings</title>{BASE_STYLE}</head><body>
<div class="container"><div class="user-bar">Logged in as: <strong style="color:#c45a1a;">{session['username']}</strong> [{rl}]</div>
<div class="nav"><a href="/calculator">Calculator</a><a href="/settings" class="active">Settings</a><a href="/logout" class="logout">Logout</a></div>
<h1>Settings</h1>
{"<p class='flash flash-" + msg[0] + "'>" + msg[1] + "</p>" if msg else ""}
<form method="POST"><h2>Change Password</h2>
<label>Current Password</label><input type="password" name="current_password" required>
<label>New Password</label><input type="password" name="new_password" required>
<button type="submit" class="btn">Update Password</button></form>
</div></body></html>"""

# -- Save Favourites --
@flask_app.route("/save_favourites", methods=["POST"])
@login_required
def save_favourites():
    data = load_accounts()
    user = data["users"].get(session["username"])
    if not user:
        return jsonify({"status": "error"}), 404
    favs = request.get_json().get("favourites", [])
    if "settings" not in user:
        user["settings"] = {}
    user["settings"]["favourites"] = favs
    save_accounts(data)
    return jsonify({"status": "ok"})

# -- Admin --
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
            new_pw = request.form.get("new_password", "")
            new_role = request.form.get("new_role", "staff")
            if new_user and new_pw and new_user not in data["users"]:
                if new_role not in data["roles"]:
                    new_role = "staff"
                data["users"][new_user] = {
                    "password_hash": hash_password(new_pw),
                    "role": new_role,
                    "discord_id": None,
                    "settings": {}
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
                for uname, uinfo in data["users"].items():
                    if uinfo.get("role") == role_name:
                        uinfo["role"] = "staff"
                del data["roles"][role_name]
                save_accounts(data)
                msg = ("ok", f"Role '{role_name}' removed.")
            else:
                msg = ("error", "Role not found.")
    data = load_accounts()
    role_label = get_user_role_label(session["username"], data)
    users_html = ""
    for name, info in data["users"].items():
        ur = info.get("role", "staff")
        rl = data["roles"].get(ur, {}).get("label", ur)
        users_html += f"<tr><td>{name}</td><td>{rl}</td><td>{info.get('discord_id','—')}</td></tr>"
    role_options = "".join(
        f"<option value='{rn}'>{ri['label']} (Level {ri['level']})</option>"
        for rn, ri in data["roles"].items()
    )
    roles_html = ""
    for rn, ri in data["roles"].items():
        roles_html += f"<tr><td>{ri['label']}</td><td>{rn}</td><td>{ri['level']}</td></tr>"
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
<table><thead><tr><th>Username</th><th>Role</th><th>Discord ID</th></tr></thead><tbody>{users_html}</tbody></table>
<h2>Add User</h2>
<form method="POST"><input type="hidden" name="action" value="add">
<label>Username</label><input type="text" name="new_username" required>
<label>Password</label><input type="password" name="new_password" required>
<label>Role</label><select name="new_role">{role_options}</select>
<button type="submit" class="btn">Create Account</button></form>
<h2>Remove User</h2>
<form method="POST"><input type="hidden" name="action" value="remove">
<label>Username</label><input type="text" name="remove_user" required>
<button type="submit" class="btn btn-danger btn-sm">Remove</button></form>
<h2>Change Role</h2>
<form method="POST"><input type="hidden" name="action" value="change_role">
<label>Username</label><input type="text" name="role_user" required>
<label>New Role</label><select name="role_value">{role_options}</select>
<button type="submit" class="btn">Change Role</button></form>
{"<hr style='border-color:#1a1a1a;margin:40px 0;'><h2 style='color:#c45a1a;'>Role Management [CEO]</h2>" if is_ceo else ""}
{"<h3>Current Roles</h3><table><thead><tr><th>Label</th><th>Name</th><th>Level</th></tr></thead><tbody>" + roles_html + "</tbody></table>" if is_ceo else ""}
{"<h3>Add New Role</h3><form method='POST'><input type='hidden' name='action' value='add_role'><label>Role Name (internal, lowercase)</label><input type='text' name='role_name' required><label>Display Label</label><input type='text' name='role_label' required><label>Clearance Level (1-99)</label><input type='number' name='role_level' value='30' min='1' max='99'><button type='submit' class='btn'>Create Role</button></form>" if is_ceo else ""}
{"<h3>Remove Role</h3><form method='POST'><input type='hidden' name='action' value='remove_role'><label>Role Name</label><input type='text' name='remove_role' required><button type='submit' class='btn btn-danger btn-sm'>Remove Role</button></form><p style='color:#666;font-size:11px;'>Users with this role will be reassigned to Staff.</p>" if is_ceo else ""}
{"<h3>Export Backup</h3><p style='color:#888;'>Copy this JSON and paste into <code>ACCOUNTS_BACKUP</code> env var on Render.</p><div class='backup-box' id='backup-json'>" + backup_json + "</div><button class='btn btn-sm' onclick='copyBackup()' style='margin-top:8px;'>Copy to Clipboard</button>" if is_ceo else ""}
</div>
{"<script>function copyBackup(){{var t=document.getElementById('backup-json').innerText;navigator.clipboard.writeText(t).then(function(){{alert('Backup copied.');}});}}</script>" if is_ceo else ""}
</body></html>"""

# -- Health --
@flask_app.route("/health")
def health():
    return "NORAI operational."

# ============================================================
# SPANSH ROUTE SCANNER — With per-commodity diagnostics
# ============================================================

@flask_app.route("/api/scan-commodity", methods=["POST"])
@login_required
def scan_commodity():
    data = request.get_json()
    system = data.get("system", "").strip()
    station = data.get("station", "").strip()
    commodities = data.get("commodities", [])
    tonnage = float(data.get("tonnage", 25000))
    safety = float(data.get("safety_margin", 5000))
    comm_load = float(data.get("commission_load", 15000))
    comm_unload = float(data.get("commission_unload", 15000))
    min_profit_total = float(data.get("min_profit_total", 250000000))
    max_distance = float(data.get("max_distance", 50000))

    if not system or not station:
        return jsonify({"error": "System and station are required."})
    if not commodities:
        return jsonify({"error": "No commodities provided."})

    req_vol = tonnage + safety
    all_trades = []
    per_commodity = []

    for commodity in commodities:
        commodity_result = {
            "commodity": commodity,
            "status": "pending",
            "routes_found": 0,
            "trades_passing_filters": 0,
            "error": None
        }
        try:
            params = {
                "commodity": commodity,
                "system": system,
                "station": station,
                "max_distance": int(max_distance),
                "limit": 10
            }
            logger.info(f"Spansh request: {commodity} from {system}/{station}")
            resp = requests.get(SPANSH_ROUTE_URL, params=params, timeout=15)
            resp.raise_for_status()
            job_data = resp.json()

            if job_data.get("error"):
                commodity_result["status"] = "error"
                commodity_result["error"] = job_data["error"]
                per_commodity.append(commodity_result)
                continue

            job_id = job_data.get("job")
            if not job_id:
                commodity_result["status"] = "error"
                commodity_result["error"] = "No job ID returned"
                per_commodity.append(commodity_result)
                continue

            # Poll for results (up to 30 seconds)
            result_data = None
            for attempt in range(15):
                time.sleep(2)
                result_resp = requests.get(f"{SPANSH_RESULT_URL}/{job_id}", timeout=10)
                result_resp.raise_for_status()
                result_data = result_resp.json()
                state = result_data.get("state", result_data.get("status", ""))
                if state in ("completed", "ok"):
                    break
                elif state in ("error", "failed"):
                    logger.warning(f"Spansh job {job_id} state={state} for {commodity}")
                    result_data = None
                    break
            else:
                logger.warning(f"Spansh job {job_id} timed out for {commodity}")
                commodity_result["status"] = "timeout"
                per_commodity.append(commodity_result)
                continue

            if not result_data:
                commodity_result["status"] = "error"
                commodity_result["error"] = "Job failed or timed out"
                per_commodity.append(commodity_result)
                continue

            routes = result_data.get("result", [])
            commodity_result["status"] = "ok"
            commodity_result["routes_found"] = len(routes)

            for route in routes:
                source_info = route.get("source", {})
                dest_info = route.get("destination", {})
                for comm in route.get("commodities", []):
                    sc = comm.get("source_commodity", {})
                    dc = comm.get("destination_commodity", {})
                    buy_price = sc.get("buy_price", 0)
                    sell_price = dc.get("sell_price", 0)
                    supply = sc.get("supply", 0)
                    demand = dc.get("demand", 0)
                    amount = comm.get("amount", 0)

                    # Volume check
                    if supply < req_vol and amount < req_vol:
                        continue
                    if demand < req_vol:
                        continue

                    trade_volume = min(amount, tonnage)
                    spread = sell_price - buy_price
                    carrier_ppt = spread - comm_load - comm_unload
                    carrier_total = carrier_ppt * trade_volume

                    if carrier_total < min_profit_total:
                        continue

                    commodity_result["trades_passing_filters"] += 1
                    all_trades.append({
                        "commodity": comm.get("name", commodity),
                        "source_system": source_info.get("system", "?"),
                        "source_station": source_info.get("station", "?"),
                        "dest_system": dest_info.get("system", "?"),
                        "dest_station": dest_info.get("station", "?"),
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "supply": supply,
                        "demand": demand,
                        "available_amount": amount,
                        "trade_volume": trade_volume,
                        "distance_ly": route.get("distance", 0),
                        "spread": spread,
                        "carrier_profit_per_ton": carrier_ppt,
                        "carrier_total_profit": carrier_total
                    })

        except requests.RequestException as e:
            commodity_result["status"] = "error"
            commodity_result["error"] = str(e)[:100]
            logger.error(f"Spansh request error for {commodity}: {e}")
        except Exception as e:
            commodity_result["status"] = "error"
            commodity_result["error"] = str(e)[:100]
            logger.error(f"Unexpected error scanning {commodity}: {e}")

        per_commodity.append(commodity_result)

    seen = {}
    for t in all_trades:
        key = f"{t['commodity']}|{t['source_system']}|{t['dest_system']}"
        if key not in seen or t["carrier_total_profit"] > seen[key]["carrier_total_profit"]:
            seen[key] = t
    unique_trades = sorted(seen.values(), key=lambda x: x["carrier_total_profit"], reverse=True)

    return jsonify({
        "trades": unique_trades[:30],
        "total_trades": len(unique_trades),
        "per_commodity": per_commodity,
        "system": system,
        "station": station
    })

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
    now = datetime.now(timezone.utc)
    ig_year = now.year + 1286
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    return ts.replace(str(now.year), str(ig_year), 1)

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

@bot.tree.command(name="status", description="NORAI system status.")
async def cmd_status(interaction: discord.Interaction):
    fleet = fetch_fleet()
    if fleet:
        active = sum(1 for s in fleet if s.get("status", "").upper() == "ACTIVE")
        fleet_summary = f"{active}/{len(fleet)} operational"
    else:
        fleet_summary = "data unavailable"
    await interaction.response.send_message(
        STATUS_RESPONSE.format(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            fleet_summary=fleet_summary,
            contract_count="0",
            ceo_status=ceo_status,
        )
    )

@bot.tree.command(name="fleet", description="NORTHCORP fleet registry.")
async def cmd_fleet(interaction: discord.Interaction):
    fleet = fetch_fleet()
    if not fleet:
        await interaction.response.send_message("Fleet data unavailable.", ephemeral=True)
        return
    lines = ["**NORTHCORP — Active Fleet Registry**\n"]
    for ship in fleet:
        lines.append(
            f"- **{ship['name']}** [{ship['identifier']}] — {ship['class']} — "
            f"{ship['role']} — Value: {ship['value']} — **{ship['status']}**"
        )
    lines.append("\nFleet carrier operational. Use **/contact** for contracts.")
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="about", description="About NORTHCORP.")
async def cmd_about(interaction: discord.Interaction):
    await interaction.response.send_message(ABOUT_RESPONSE)

@bot.tree.command(name="services", description="NORTHCORP services.")
async def cmd_services(interaction: discord.Interaction):
    await interaction.response.send_message(SERVICES_RESPONSE)

@bot.tree.command(name="contact", description="Contact CEO North.")
async def cmd_contact(interaction: discord.Interaction):
    await interaction.response.send_message(CONTACT_RESPONSE)

@bot.tree.command(name="help", description="Available commands.")
async def cmd_help(interaction: discord.Interaction):
    await interaction.response.send_message("""**NORAI — Available Commands**

```
/status    — System status and CEO location
/fleet     — Fleet registry
/about     — About NORTHCORP
/services  — Services offered
/contact   — Contact CEO North
/clock     — In-game date and time
/quote     — Random NORTHCORP motto
/trade     — Quick trade profit calculator
/log       — Submit a flight log (Contractor+)
/help      — This list
```

I also respond to: "NORAI" and "NORAI, report"

**NORAI Trade Calculator:** Employee login required for live route scanning.""")

@bot.tree.command(name="clock", description="In-game date and time.")
async def cmd_clock(interaction: discord.Interaction):
    ig_time = in_game_datetime()
    utc_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await interaction.response.send_message(
        f"**Galactic Standard Time**\n\n- **In-Game:** {ig_time}\n- **UTC:** {utc_time}\n\n*NORTHCORP operates on 24-hour GST.*"
    )

@bot.tree.command(name="quote", description="Random NORTHCORP motto.")
async def cmd_quote(interaction: discord.Interaction):
    await interaction.response.send_message(f"*{random.choice(QUOTES)}*")

@bot.tree.command(name="trade", description="Quick trade profit calculator.")
@app_commands.describe(
    commodity="Commodity name",
    buy_price="Buy price per ton at station (CR)",
    sell_price="Sell price per ton at destination (CR)",
    tonnage="Tonnage to haul",
    commission="Commission per ton in CR"
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
        await interaction.response.send_message("All values must be positive.", ephemeral=True)
        return
    buy_cost = buy_price * tonnage
    sell_rev = sell_price * tonnage
    gross = sell_rev - buy_cost
    comm_amt = commission * tonnage
    net = gross - comm_amt
    ppt = net / tonnage
    await interaction.response.send_message(f"""**Trade Calculation — {commodity}**

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

*For detailed route scanning with live market data, use the [NORAI Trade Calculator](https://northcorp-norai.onrender.com/calculator).*""")

@bot.tree.command(name="log", description="[Contractor+] Submit a flight log.")
@app_commands.describe(
    ship="Ship used", cargo="Cargo type and tonnage",
    origin="Origin", destination="Destination",
    profit="Profit earned", notes="Additional notes (optional)"
)
async def cmd_log(
    interaction: discord.Interaction,
    ship: str, cargo: str, origin: str, destination: str, profit: str,
    notes: str = ""
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        await interaction.response.send_message("Unable to verify permissions.", ephemeral=True)
        return
    role_names = [r.name for r in member.roles]
    if "CEO" not in role_names and "Contractor" not in role_names:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notes_str = notes if notes else "N/A"
    entry = (
        f"**FLIGHT LOG — {timestamp}**\n\n```\nShip:        {ship}\nCargo:       {cargo}\n"
        f"Origin:      {origin}\nDestination: {destination}\nProfit:      {profit}\nNotes:       {notes_str}\n```\n\n"
        f"*Logged by {interaction.user.display_name} — NORTHCORP [NINC]*"
    )
    channel = bot.get_channel(CHANNEL_FLIGHT_LOGS)
    if channel is None:
        await interaction.response.send_message("Error: Flight log channel not found.", ephemeral=True)
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

@bot.tree.command(name="setstatus", description="[CEO] Set your current location/status.")
@app_commands.describe(status="Your current status")
async def cmd_setstatus(interaction: discord.Interaction, status: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    global ceo_status
    ceo_status = status
    await interaction.response.send_message(f"CEO status updated: **{status}**", ephemeral=True)

@bot.tree.command(name="broadcast", description="[CEO] Broadcast to Company Bulletins.")
@app_commands.describe(message="Message to broadcast")
async def cmd_broadcast(interaction: discord.Interaction, message: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    channel = bot.get_channel(CHANNEL_BULLETINS)
    if channel is None:
        await interaction.response.send_message("Error: Bulletins channel not found.", ephemeral=True)
        return
    await channel.send(f"**NORTHCORP BULLETIN** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n{message}")
    await interaction.response.send_message("Bulletin transmitted.", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"Cooldown — retry in {error.retry_after:.0f}s.", ephemeral=True)
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
