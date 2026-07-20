import os
import json
import random
import hashlib
import secrets
import logging
import threading
from datetime import datetime, timezone
from functools import wraps

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, redirect
import requests

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [NORAI] %(levelname)s: %(message)s")
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
    if data is None: data = load_accounts()
    user = data["users"].get(username)
    if not user: return 0
    role = data["roles"].get(user.get("role", "staff"), {})
    return role.get("level", 0)

def get_user_role_label(username, data=None):
    if data is None: data = load_accounts()
    user = data["users"].get(username)
    if not user: return "Unknown"
    role = data["roles"].get(user.get("role", "staff"), {})
    return role.get("label", user.get("role", "staff"))

def ensure_defaults():
    data = load_accounts()
    for name, info in DEFAULT_ROLES.items():
        if name not in data["roles"]: data["roles"][name] = info
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
            if len(backup.get("users", {})) > len(data["users"]):
                logger.info("Restoring accounts from backup.")
                data = backup
                if "roles" not in data: data["roles"] = dict(DEFAULT_ROLES)
                data["users"]["k.north"]["role"] = "ceo"
                save_accounts(data)
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
    return data

def get_backup_json():
    return json.dumps(load_accounts())

flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session: return redirect("/")
        return f(*args, **kwargs)
    return decorated

def min_level(level):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "username" not in session: return redirect("/")
            if get_user_level(session["username"]) < level:
                return "Access denied.", 403
            return f(*args, **kwargs)
        return decorated
    return decorator

STYLE = """
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#c0c0c0;font-family:"Courier New",Courier,monospace;line-height:1.6;font-size:15px}
.container{max-width:800px;margin:0 auto;padding:40px 24px}
h1{color:#c45a1a;font-size:28px;letter-spacing:4px;margin-bottom:20px}
h2{color:#c45a1a;font-size:16px;letter-spacing:2px;margin:20px 0 12px}
label{display:block;color:#888;font-size:11px;letter-spacing:1px;text-transform:uppercase;margin:8px 0 3px}
input{background:#0f0f0f;color:#e0e0e0;border:1px solid #1a1a1a;padding:10px 14px;font-family:"Courier New",monospace;font-size:14px;width:100%;max-width:300px}
input:focus{border-color:#c45a1a;outline:none}
input.result{background:#0f0f0f;color:#c45a1a;font-weight:bold;font-size:18px;border-color:#3a2a10;max-width:350px}
input.calc{color:#e0e0e0}
.row{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:4px}
.col{flex:1;min-width:180px}
.inara-link{display:inline-block;margin-top:4px;color:#666;font-size:11px;text-decoration:none;border-bottom:1px dotted #444}
.inara-link:hover{color:#c45a1a;border-color:#c45a1a}
.result-box{background:#0f0f0f;border:1px solid #2a1a0c;padding:24px;margin-top:24px}
.result-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.ri-label{color:#666;font-size:10px;text-transform:uppercase;letter-spacing:1px}
.ri-value{color:#e0e0e0;font-size:18px;font-weight:bold}
.green{color:#6a9a5b!important}
.red{color:#a04040!important}
.amber{color:#c4a040!important}
.comm-warn{margin-top:12px}
.flash-error{border-color:#a04040;color:#c06060;background:#150808;padding:10px 14px;margin-top:8px;font-size:12px}
.nav{display:flex;gap:24px;margin-bottom:30px;border-bottom:1px solid #1a1a1a;padding-bottom:12px}
.nav a{color:#888;text-decoration:none;font-size:13px;letter-spacing:1px;text-transform:uppercase}
.nav a:hover,.nav a.active{color:#c45a1a}
.nav .logout{margin-left:auto;color:#666}
.user-bar{color:#666;font-size:12px;margin-bottom:8px}
@media(max-width:600px){.container{padding:20px 16px}.result-grid{grid-template-columns:1fr}}
</style>
"""

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
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>NORAI Trade Calculator — Login</title>{STYLE}</head><body>
<div class="container" style="max-width:400px;padding-top:80px">
<h1>NORAI Trade Calculator</h1>
<p style="color:#888;margin-bottom:24px">NORTHCORP employee access.</p>
{"<p class='flash-error'>" + error + "</p>" if error else ""}
<form method="POST">
<label>Username</label><input type="text" name="username" required>
<label>Password</label><input type="password" name="password" required>
<button style="margin-top:16px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:12px 28px;border:none;cursor:pointer;font-family:'Courier New',monospace">Log In</button>
</form>
<p style="color:#666;font-size:12px;margin-top:24px">No account? Contact CEO North.</p>
</div></body></html>"""

@flask_app.route("/logout")
def logout_page():
    session.clear()
    return redirect("/")

@flask_app.route("/calculator")
@login_required
def calculator_page():
    data = load_accounts()
    rl = get_user_role_label(session["username"], data)
    is_admin = get_user_level(session["username"], data) >= 50
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>NORAI Trade Calculator</title>{STYLE}</head><body>
<div class="container">
<div class="user-bar">Logged in: <strong style="color:#c45a1a">{session['username']}</strong> [{rl}]</div>
<div class="nav">
<a href="/calculator" class="active">Calculator</a>
{"<a href='/admin'>Admin</a>" if is_admin else ""}
<a href="/settings">Settings</a>
<a href="/logout" class="logout">Logout</a>
</div>

<h1>Carrier Trade Calculator</h1>
<p style="color:#888;margin-bottom:20px">Use Inara to find a commodity with a low buy price and high supply. Paste the best buy price here to see what sell price you need, or fill in what you know and the calculator fills the rest.</p>

<h2>Commodity</h2>
<div class="row">
  <div class="col">
    <label>Commodity Name</label>
    <input type="text" id="commodity" class="calc" placeholder="e.g. Rhodplumsite" oninput="recalc()">
    <a id="inara-buy-link" class="inara-link" href="#" target="_blank">Search Inara for buy prices &rarr;</a>
  </div>
</div>

<h2>Inputs — Fill what you know</h2>
<p style="color:#666;font-size:11px;margin-bottom:8px">Leave ONE field blank and it will be auto-calculated. Leave more than one blank and results will update as you type when enough is known.</p>

<div class="row">
  <div class="col">
    <label>Station Buy Price (CR/t)</label>
    <input type="number" id="buy" class="calc" placeholder="Found on Inara" step="1" min="0" oninput="recalc()">
  </div>
  <div class="col">
    <label>Loading Commission (CR/t)</label>
    <input type="number" id="loadComm" class="calc" placeholder="Min 10,000" step="1" min="0" value="15000" oninput="recalc()">
  </div>
</div>
<div class="row">
  <div class="col">
    <label>Unloading Commission (CR/t)</label>
    <input type="number" id="unloadComm" class="calc" placeholder="Min 10,000" step="1" min="0" value="15000" oninput="recalc()">
  </div>
  <div class="col">
    <label>Tonnage</label>
    <input type="number" id="tons" class="calc" placeholder="25000" step="1" min="0" value="25000" oninput="recalc()">
  </div>
</div>
<div class="row">
  <div class="col">
    <label>Desired Carrier Profit Per Ton (CR/t)</label>
    <input type="number" id="profitTon" class="calc" placeholder="Leave blank to calculate" step="1" min="0" oninput="recalc()">
  </div>
  <div class="col">
    <label>Desired Carrier Total Profit (CR)</label>
    <input type="number" id="profitTotal" class="calc" placeholder="e.g. 250,000,000" step="1" min="0" oninput="recalc()">
  </div>
</div>
<div class="row">
  <div class="col">
    <label>Station Sell Price (CR/t) — REQUIRED</label>
    <input type="number" id="sell" class="result" placeholder="Calculated..." step="1" min="0" oninput="recalc()">
    <a id="inara-sell-link" class="inara-link" href="#" target="_blank">Search Inara for stations buying at this price &rarr;</a>
  </div>
</div>

<div id="commission-warn"></div>

<div id="results" class="result-box hidden">
<h2>Summary</h2>
<div class="result-grid" id="result-grid"></div>
</div>

</div>

<script>
function fmtCR(n){{if(Math.abs(n)>=1e9)return(n/1e9).toFixed(2)+'B CR';if(Math.abs(n)>=1e6)return(n/1e6).toFixed(1)+'M CR';if(Math.abs(n)>=1e3)return(n/1e3).toFixed(0)+'K CR';return Math.round(n).toLocaleString('en-US')+' CR'}}

function fmt(n){{return Math.round(n).toLocaleString('en-US')+' CR'}}

function val(id){{var v=document.getElementById(id).value.trim();return v===''?null:parseFloat(v)}}

function setVal(id,v){{document.getElementById(id).value=v!==null?Math.round(v):''}}

function recalc(){{
  var comm=document.getElementById('commodity').value.trim()||'Commodity';
  var buy=val('buy');
  var lc=val('loadComm');
  var uc=val('unloadComm');
  var tons=val('tons');
  var ppt=val('profitTon');
  var tpt=val('profitTotal');
  var sell=val('sell');

  // Update Inara links
  var commEnc=encodeURIComponent(comm);
  document.getElementById('inara-buy-link').href='https://inara.cz/elite/commodities/?name='+commEnc;
  if(sell!==null){{document.getElementById('inara-sell-link').href='https://inara.cz/elite/commodities/?name='+commEnc}}else{{document.getElementById('inara-sell-link').href='#'}}

  // Count how many are empty
  var blanks=[];
  if(buy===null)blanks.push('buy');
  if(lc===null)blanks.push('loadComm');
  if(uc===null)blanks.push('unloadComm');
  if(tons===null)blanks.push('tons');
  if(ppt===null&&tpt===null)blanks.push('profit');
  if(sell===null)blanks.push('sell');
  // profitTon and profitTotal are two views of the same value
  // If both are filled, they must agree (use ppt = tpt/tons)
  // Determine profit per ton
  var profitPerTon=null;
  if(ppt!==null&&tons!==null){{profitPerTon=ppt;}}
  else if(tpt!==null&&tons!==null&&tons>0){{profitPerTon=tpt/tons;}}
  else if(ppt!==null){{profitPerTon=ppt;}}

  // If we have exactly one blank, calculate it
  if(blanks.length===1){{
    var b=blanks[0];
    if(b==='sell'&&buy!==null&&lc!==null&&uc!==null&&profitPerTon!==null){{
      sell=buy+lc+uc+profitPerTon;setVal('sell',sell);
    }}else if(b==='buy'&&sell!==null&&lc!==null&&uc!==null&&profitPerTon!==null){{
      buy=sell-lc-uc-profitPerTon;setVal('buy',buy);
    }}else if(b==='loadComm'&&buy!==null&&sell!==null&&uc!==null&&profitPerTon!==null){{
      lc=sell-buy-uc-profitPerTon;setVal('loadComm',lc);
    }}else if(b==='unloadComm'&&buy!==null&&sell!==null&&lc!==null&&profitPerTon!==null){{
      uc=sell-buy-lc-profitPerTon;setVal('unloadComm',uc);
    }}else if(b==='tons'&&buy!==null&&sell!==null&&lc!==null&&uc!==null&&tpt!==null&&profitPerTon!==null&&profitPerTon>0){{
      tons=tpt/profitPerTon;setVal('tons',tons);
    }}else if(b==='profit'&&buy!==null&&sell!==null&&lc!==null&&uc!==null){{
      profitPerTon=sell-buy-lc-uc;setVal('profitTon',profitPerTon);
    }}
    // Re-read values after auto-fill
    buy=val('buy');lc=val('loadComm');uc=val('unloadComm');
    sell=val('sell');tons=val('tons');ppt=val('profitTon');tpt=val('profitTotal');
    if(ppt===null&&tons!==null&&tons>0&&tpt!==null)ppt=tpt/tons;
    if(ppt!==null&&tons!==null&&tpt===null)tpt=ppt*tons;
  }}

  // Update profit total if we have ppt and tons
  if(ppt!==null&&tons!==null&&tpt===null&&document.getElementById('profitTotal').value===''){{
    setVal('profitTotal',ppt*tons);
    tpt=ppt*tons;
  }}
  if(ppt===null&&tpt!==null&&tons!==null&&tons>0&&document.getElementById('profitTon').value===''){{
    setVal('profitTon',tpt/tons);
    ppt=tpt/tons;
  }}

  // Show results if we have enough data
  if(buy===null||sell===null||lc===null||uc===null||tons===null){{
    document.getElementById('results').classList.add('hidden');
    document.getElementById('commission-warn').innerHTML='';
    document.getElementById('inara-sell-link').href='#';
    return;
  }}

  profitPerTon=sell-buy-lc-uc;
  if(ppt===null){{setVal('profitTon',profitPerTon);ppt=profitPerTon;}}
  if(tpt===null){{setVal('profitTotal',profitPerTon*tons);tpt=profitPerTon*tons;}}

  var carrierBuyOrder=buy+lc;
  var carrierSellOrder=sell-uc;
  var loadCost=carrierBuyOrder*tons;
  var unloadRev=carrierSellOrder*tons;
  var net=unloadRev-loadCost;
  var roi=carrierBuyOrder>0?(profitPerTon/carrierBuyOrder)*100:0;

  document.getElementById('inara-sell-link').href='https://inara.cz/elite/commodities/?name='+commEnc;

  var html='';
  html+='<div class="ri-label">Carrier Buy Order Price</div><div class="ri-value red">'+fmt(carrierBuyOrder)+' /t</div>';
  html+='<div class="ri-label">Carrier Sell Order Price</div><div class="ri-value green">'+fmt(carrierSellOrder)+' /t</div>';
  html+='<div class="ri-label">Total Loading Cost</div><div class="ri-value red">'+fmtCR(loadCost)+'</div>';
  html+='<div class="ri-label">Total Unloading Revenue</div><div class="ri-value green">'+fmtCR(unloadRev)+'</div>';
  html+='<div class="ri-label">Station Spread</div><div class="ri-value amber">'+fmt(sell-buy)+' /t</div>';
  html+='<div class="ri-label">Carrier Net Profit</div><div class="ri-value green">'+fmtCR(net)+'</div>';
  html+='<div class="ri-label">Profit Per Ton</div><div class="ri-value green">'+fmt(profitPerTon)+' /t</div>';
  html+='<div class="ri-label">ROI</div><div class="ri-value">'+roi.toFixed(1)+'%</div>';
  html+='<div class="ri-label">Loading Pilot Earns</div><div class="ri-value">'+fmt(lc)+' /t</div>';
  html+='<div class="ri-label">Unloading Pilot Earns</div><div class="ri-value">'+fmt(uc)+' /t</div>';
  document.getElementById('result-grid').innerHTML=html;
  document.getElementById('results').classList.remove('hidden');

  var warn='';
  if(lc<10000)warn+='<div class="flash-error">Loading commission below 10,000 CR/t PTN minimum.</div>';
  if(uc<10000)warn+='<div class="flash-error">Unloading commission below 10,000 CR/t PTN minimum.</div>';
  if(profitPerTon<0)warn+='<div class="flash-error">NET LOSS: carrier loses '+fmt(-profitPerTon)+' per ton.</div>';
  document.getElementById('commission-warn').innerHTML=warn;
}}

// Run once on load to fill in defaults
window.onload=function(){{recalc();}};
</script>
</body></html>"""

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
            msg = ("flash-ok", "Password changed.")
        else:
            msg = ("flash-error", "Current password incorrect.")
    rl = get_user_role_label(session["username"])
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Settings</title>{STYLE}</head><body>
<div class="container"><div class="user-bar">Logged in: <strong style="color:#c45a1a">{session['username']}</strong> [{rl}]</div>
<div class="nav"><a href="/calculator">Calculator</a><a href="/settings" class="active">Settings</a><a href="/logout" class="logout">Logout</a></div>
<h1>Settings</h1>
{"<p class='" + msg[0] + "'>" + msg[1] + "</p>" if msg else ""}
<form method="POST"><h2>Change Password</h2>
<label>Current Password</label><input type="password" name="current_password" required>
<label>New Password</label><input type="password" name="new_password" required>
<button style="margin-top:16px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:12px 28px;border:none;cursor:pointer;font-family:'Courier New',monospace">Update Password</button></form>
</div></body></html>"""

@flask_app.route("/admin", methods=["GET", "POST"])
@min_level(50)
def admin_page():
    msg = None
    user_level = get_user_level(session["username"])
    is_ceo = user_level >= 100
    if request.method == "POST":
        action = request.form.get("action", "")
        data = load_accounts()
        if action == "add":
            nu = request.form.get("new_username", "").lower().strip()
            np = request.form.get("new_password", "")
            nr = request.form.get("new_role", "staff")
            if nu and np and nu not in data["users"]:
                if nr not in data["roles"]: nr = "staff"
                data["users"][nu] = {"password_hash": hash_password(np), "role": nr, "discord_id": None, "settings": {}}
                save_accounts(data)
                msg = ("flash-ok", f"Account '{nu}' created.")
            else:
                msg = ("flash-error", "Invalid or duplicate username.")
        elif action == "remove":
            t = request.form.get("remove_user", "").lower().strip()
            if t == "k.north":
                msg = ("flash-error", "Cannot remove CEO.")
            elif t in data["users"]:
                if user_level <= get_user_level(t, data):
                    msg = ("flash-error", "Cannot remove equal/higher clearance.")
                else:
                    del data["users"][t]; save_accounts(data)
                    msg = ("flash-ok", f"'{t}' removed.")
            else: msg = ("flash-error", "User not found.")
        elif action == "change_role":
            t = request.form.get("role_user", "").lower().strip()
            nr = request.form.get("role_value", "staff")
            if t == "k.north": msg = ("flash-error", "Cannot change CEO role.")
            elif nr not in data["roles"]: msg = ("flash-error", "Invalid role.")
            elif t in data["users"]:
                if user_level <= get_user_level(t, data) and not is_ceo:
                    msg = ("flash-error", "Cannot change equal/higher clearance.")
                else:
                    data["users"][t]["role"] = nr; save_accounts(data)
                    msg = ("flash-ok", f"Role for '{t}' set to '{nr}'.")
            else: msg = ("flash-error", "User not found.")
        elif action == "add_role" and is_ceo:
            rn = request.form.get("role_name", "").lower().strip()
            rl = request.form.get("role_label", "").strip()
            rlv = int(request.form.get("role_level", "10"))
            if rn and rl and rn not in data["roles"]:
                if rn == "ceo": msg = ("flash-error", "Cannot use 'ceo'.")
                elif rlv >= 100: msg = ("flash-error", "Level must be < 100.")
                else:
                    data["roles"][rn] = {"level": rlv, "label": rl}; save_accounts(data)
                    msg = ("flash-ok", f"Role '{rl}' created.")
            else: msg = ("flash-error", "Invalid or duplicate.")
        elif action == "remove_role" and is_ceo:
            rn = request.form.get("remove_role", "").lower().strip()
            if rn in ("ceo", "staff"): msg = ("flash-error", "Cannot remove CEO/Staff.")
            elif rn in data["roles"]:
                for uname, uinfo in data["users"].items():
                    if uinfo.get("role") == rn: uinfo["role"] = "staff"
                del data["roles"][rn]; save_accounts(data)
                msg = ("flash-ok", f"Role '{rn}' removed.")
            else: msg = ("flash-error", "Role not found.")
    data = load_accounts()
    rl = get_user_role_label(session["username"], data)
    users_html = ""
    for name, info in data["users"].items():
        ur = info.get("role", "staff")
        rlv = data["roles"].get(ur, {}).get("label", ur)
        users_html += f"<tr><td>{name}</td><td>{rlv}</td><td>{info.get('discord_id','—')}</td></tr>"
    role_options = "".join(f"<option value='{rn}'>{ri['label']} (Lv {ri['level']})</option>" for rn, ri in data["roles"].items())
    roles_html = "".join(f"<tr><td>{ri['label']}</td><td>{rn}</td><td>{ri['level']}</td></tr>" for rn, ri in data["roles"].items())
    backup_json = get_backup_json()
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Admin</title>{STYLE}</head><body>
<div class="container">
<div class="user-bar">Logged in: <strong style="color:#c45a1a">{session['username']}</strong> [{rl}]</div>
<div class="nav"><a href="/calculator">Calculator</a><a href="/admin" class="active">Admin</a><a href="/settings">Settings</a><a href="/logout" class="logout">Logout</a></div>
<h1>Account Management</h1>
{"<p class='" + msg[0] + "'>" + msg[1] + "</p>" if msg else ""}
<h2>Registered Users</h2>
<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666">Username</th><th style="text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666">Role</th><th style="text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666">Discord</th></tr></thead><tbody>{users_html}</tbody></table>
<h2>Add User</h2>
<form method="POST"><input type="hidden" name="action" value="add">
<label>Username</label><input type="text" name="new_username" required>
<label>Password</label><input type="password" name="new_password" required>
<label>Role</label><select name="new_role" style="background:#0f0f0f;color:#e0e0e0;border:1px solid #1a1a1a;padding:8px;font-family:'Courier New';width:100%;max-width:300px">{role_options}</select>
<button style="margin-top:12px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:10px 24px;border:none;cursor:pointer;font-family:'Courier New',monospace">Create Account</button></form>
<h2>Remove User</h2>
<form method="POST"><input type="hidden" name="action" value="remove">
<label>Username</label><input type="text" name="remove_user" required>
<button style="margin-top:8px;background:#8b3030;color:#e0e0e0;font-weight:bold;font-size:11px;letter-spacing:1px;padding:8px 18px;border:none;cursor:pointer;font-family:'Courier New',monospace">Remove</button></form>
<h2>Change Role</h2>
<form method="POST"><input type="hidden" name="action" value="change_role">
<label>Username</label><input type="text" name="role_user" required>
<label>New Role</label><select name="role_value" style="background:#0f0f0f;color:#e0e0e0;border:1px solid #1a1a1a;padding:8px;font-family:'Courier New';width:100%;max-width:300px">{role_options}</select>
<button style="margin-top:12px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:10px 24px;border:none;cursor:pointer;font-family:'Courier New',monospace">Change Role</button></form>
{"<hr style='border-color:#1a1a1a;margin:30px 0'><h2 style='color:#c45a1a'>Role Management [CEO]</h2>" if is_ceo else ""}
{"<h3>Current Roles</h3><table style='width:100%;border-collapse:collapse'><thead><tr><th style='text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666'>Label</th><th style='text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666'>Name</th><th style='text-align:left;padding:8px;border-bottom:1px solid #1a1a1a;color:#666'>Level</th></tr></thead><tbody>" + roles_html + "</tbody></table>" if is_ceo else ""}
{"<h3>Add Role</h3><form method='POST'><input type='hidden' name='action' value='add_role'><label>Role Name (lowercase)</label><input type='text' name='role_name' required><label>Display Label</label><input type='text' name='role_label' required><label>Level (1-99)</label><input type='number' name='role_level' value='30' min='1' max='99'><button style='margin-top:12px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:10px 24px;border:none;cursor:pointer;font-family:\"Courier New\",monospace'>Create Role</button></form>" if is_ceo else ""}
{"<h3>Remove Role</h3><form method='POST'><input type='hidden' name='action' value='remove_role'><label>Role Name</label><input type='text' name='remove_role' required><button style='margin-top:8px;background:#8b3030;color:#e0e0e0;font-weight:bold;font-size:11px;letter-spacing:1px;padding:8px 18px;border:none;cursor:pointer;font-family:\"Courier New\",monospace'>Remove Role</button></form><p style='color:#666;font-size:11px'>Users with this role become Staff.</p>" if is_ceo else ""}
{"<h3>Export Backup</h3><p style='color:#888;font-size:12px'>Copy and paste into <code>ACCOUNTS_BACKUP</code> env var on Render.</p><div style='background:#0f0f0f;border:1px solid #2a2a0a;padding:12px;max-height:150px;overflow:auto;font-size:10px;color:#888;word-break:break-all;white-space:pre-wrap;margin-top:6px' id='backup-json'>" + backup_json + "</div><button style='margin-top:8px;background:#c45a1a;color:#0a0a0a;font-weight:bold;font-size:11px;letter-spacing:1px;padding:6px 14px;border:none;cursor:pointer;font-family:\"Courier New\",monospace' onclick='copyBackup()'>Copy</button>" if is_ceo else ""}
</div>
{"<script>function copyBackup(){{var t=document.getElementById('backup-json').innerText;navigator.clipboard.writeText(t).then(function(){{alert('Copied.');}});}}</script>" if is_ceo else ""}
</body></html>"""

@flask_app.route("/health")
def health():
    return "NORAI operational."

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
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="NORTHCORP comms | /help"))

@bot.event
async def on_member_join(member: discord.Member):
    arrivals = f"<#{CHANNEL_ARRIVALS}>" if CHANNEL_ARRIVALS else "#arrivals-dock"
    try:
        await member.send(WELCOME_MESSAGE.format(arrivals_channel=arrivals))
        logger.info(f"Welcome DM sent to {member.name}.")
    except discord.Forbidden:
        logger.warning(f"Could not DM {member.name}.")

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user: return
    await bot.process_commands(message)
    content = message.content.lower().strip()
    if content in ("hello norai", "norai", "norai, hello"):
        await message.channel.send(f"On station, {message.author.display_name}. I am NORAI. Use **/help** for commands.")
    elif content in ("norai report", "norai, report", "report norai"):
        fleet = fetch_fleet()
        if fleet:
            active = sum(1 for s in fleet if s.get("status", "").upper() == "ACTIVE")
            fleet_line = f"{active}/{len(fleet)} operational"
        else:
            fleet_line = "data unavailable"
        await message.channel.send(f"NORAI reporting. Fleet: {fleet_line}. All systems nominal. CEO: {ceo_status}. Use **/contact**.")
    elif "norai" in content and "thank" in content:
        await message.channel.send("Acknowledged. Forward gratitude to CEO North — he built this operation from the ground up.")

@bot.command(name="sync")
async def cmd_sync(ctx: commands.Context):
    if ctx.author.id != CEO_DISCORD_ID:
        await ctx.send("Access denied."); return
    await ctx.send("Syncing...")
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
    await interaction.response.send_message(STATUS_RESPONSE.format(timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), fleet_summary=fleet_summary, contract_count="0", ceo_status=ceo_status))

@bot.tree.command(name="fleet", description="NORTHCORP fleet registry.")
async def cmd_fleet(interaction: discord.Interaction):
    fleet = fetch_fleet()
    if not fleet:
        await interaction.response.send_message("Fleet data unavailable.", ephemeral=True); return
    lines = ["**NORTHCORP — Active Fleet Registry**\n"]
    for ship in fleet:
        lines.append(f"- **{ship['name']}** [{ship['identifier']}] — {ship['class']} — {ship['role']} — Value: {ship['value']} — **{ship['status']}**")
    lines.append("\nFleet carrier operational. Use **/contact** for contracts.")
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="about", description="About NORTHCORP.")
async def cmd_about(interaction: discord.Interaction): await interaction.response.send_message(ABOUT_RESPONSE)

@bot.tree.command(name="services", description="NORTHCORP services.")
async def cmd_services(interaction: discord.Interaction): await interaction.response.send_message(SERVICES_RESPONSE)

@bot.tree.command(name="contact", description="Contact CEO North.")
async def cmd_contact(interaction: discord.Interaction): await interaction.response.send_message(CONTACT_RESPONSE)

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

**NORAI Trade Calculator:** Employee login required for detailed carrier trade planning.""")

@bot.tree.command(name="clock", description="In-game date and time.")
async def cmd_clock(interaction: discord.Interaction):
    await interaction.response.send_message(f"**Galactic Standard Time**\n\n- **In-Game:** {in_game_datetime()}\n- **UTC:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n*NORTHCORP operates on 24-hour GST.*")

@bot.tree.command(name="quote", description="Random NORTHCORP motto.")
async def cmd_quote(interaction: discord.Interaction): await interaction.response.send_message(f"*{random.choice(QUOTES)}*")

@bot.tree.command(name="trade", description="Quick trade profit calculator.")
@app_commands.describe(commodity="Commodity name", buy_price="Buy price per ton (CR)", sell_price="Sell price per ton (CR)", tonnage="Tonnage", commission="Commission per ton (CR)")
async def cmd_trade(interaction: discord.Interaction, commodity: str, buy_price: float, sell_price: float, tonnage: float, commission: float = 0.0):
    if buy_price <= 0 or sell_price <= 0 or tonnage <= 0:
        await interaction.response.send_message("All values must be positive.", ephemeral=True); return
    buy_cost = buy_price * tonnage; sell_rev = sell_price * tonnage; gross = sell_rev - buy_cost; comm_amt = commission * tonnage; net = gross - comm_amt; ppt = net / tonnage
    await interaction.response.send_message(f"""**Trade Calculation — {commodity}**

```
Buy Price:    {buy_price:,.0f} CR/ton
Sell Price:   {sell_price:,.0f} CR/ton
Tonnage:      {tonnage:,.0f} t
Buy Cost:     {fmt_cr(buy_cost)}
Sell Revenue: {fmt_cr(sell_rev)}
Comm ({commission:,.0f} CR/t):  {fmt_cr(comm_amt)}
Net Profit:   {fmt_cr(net)}
Profit/Ton:   {fmt_cr(ppt)}
```
*Full carrier trade planning: [NORAI Trade Calculator](https://northcorp-norai.onrender.com/calculator)*""")

@bot.tree.command(name="log", description="[Contractor+] Submit a flight log.")
@app_commands.describe(ship="Ship used", cargo="Cargo type and tonnage", origin="Origin", destination="Destination", profit="Profit earned", notes="Additional notes (optional)")
async def cmd_log(interaction: discord.Interaction, ship: str, cargo: str, origin: str, destination: str, profit: str, notes: str = ""):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or ("CEO" not in [r.name for r in member.roles] and "Contractor" not in [r.name for r in member.roles]):
        await interaction.response.send_message("Access denied.", ephemeral=True); return
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"**FLIGHT LOG — {timestamp}**\n\n```\nShip:        {ship}\nCargo:       {cargo}\nOrigin:      {origin}\nDestination: {destination}\nProfit:      {profit}\nNotes:       {notes or 'N/A'}\n```\n\n*Logged by {interaction.user.display_name} — NORTHCORP [NINC]*"
    channel = bot.get_channel(CHANNEL_FLIGHT_LOGS)
    if channel is None: await interaction.response.send_message("Error: Flight log channel not found.", ephemeral=True); return
    if isinstance(channel, discord.ForumChannel):
        thread = await channel.create_thread(name=f"{ship} — {origin} to {destination} — {timestamp[:10]}", content=entry)
        await interaction.response.send_message(f"Flight log posted: {thread.thread.mention}", ephemeral=True)
    else:
        await channel.send(entry); await interaction.response.send_message("Flight log posted.", ephemeral=True)

@bot.tree.command(name="setstatus", description="[CEO] Set your current location/status.")
@app_commands.describe(status="Your current status")
async def cmd_setstatus(interaction: discord.Interaction, status: str):
    if interaction.user.id != CEO_DISCORD_ID: await interaction.response.send_message("Access denied.", ephemeral=True); return
    global ceo_status; ceo_status = status
    await interaction.response.send_message(f"CEO status updated: **{status}**", ephemeral=True)

@bot.tree.command(name="broadcast", description="[CEO] Broadcast to Company Bulletins.")
@app_commands.describe(message="Message to broadcast")
async def cmd_broadcast(interaction: discord.Interaction, message: str):
    if interaction.user.id != CEO_DISCORD_ID: await interaction.response.send_message("Access denied.", ephemeral=True); return
    channel = bot.get_channel(CHANNEL_BULLETINS)
    if channel is None: await interaction.response.send_message("Error: Bulletins channel not found.", ephemeral=True); return
    await channel.send(f"**NORTHCORP BULLETIN** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n{message}")
    await interaction.response.send_message("Bulletin transmitted.", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"Cooldown — retry in {error.retry_after:.0f}s.", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        await interaction.response.send_message("Internal error. Notify CEO North.", ephemeral=True)

def start_flask():
    ensure_defaults()
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    if TOKEN is None: logger.critical("DISCORD_TOKEN not found."); exit(1)
    threading.Thread(target=start_flask, daemon=True).start()
    logger.info(f"Flask started on port {PORT}.")
    bot.run(TOKEN)
