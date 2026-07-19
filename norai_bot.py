import os
import json
import random
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import requests

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

CEO_DISCORD_ID      = 779606576616964108
CHANNEL_BULLETINS   = 1528195910637719692
CHANNEL_FLIGHT_LOGS = 1528173868215177286
CHANNEL_ARRIVALS    = 1528169291873255434
GUILD_ID            = 1527555743413440533

FLEET_JSON_URL = "https://north-corp.github.io/northcorp/fleet.json"

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NORAI] %(levelname)s: %(message)s"
)
logger = logging.getLogger("NORAI")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"NORAI operational.")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()


def start_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


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
            total = len(fleet)
            fleet_line = f"{active}/{total} operational"
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
    """[CEO] Manually sync slash commands to this guild."""
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
/log       — Submit a flight log (Contractor+)
/help      — This list
```

I also respond to:
• "NORAI" or "hello NORAI"
• "NORAI, report"

For additional functionality, direct inquiries to CEO North."""
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
        await interaction.response.send_message(
            "Error: Flight log channel not found. Notify CEO North.",
            ephemeral=True
        )
        return

    if isinstance(channel, discord.ForumChannel):
        thread = await channel.create_thread(
            name=f"{ship} — {origin} to {destination} — {timestamp[:10]}",
            content=entry
        )
        await interaction.response.send_message(
            f"Flight log posted: {thread.thread.mention}",
            ephemeral=True
        )
    else:
        await channel.send(entry)
        await interaction.response.send_message(
            "Flight log posted.",
            ephemeral=True
        )


@bot.tree.command(name="setstatus", description="[CEO] Set your current location or status.")
@app_commands.describe(status="Your current status (e.g., 'In transit to Lave', 'At HQ')")
async def cmd_setstatus(interaction: discord.Interaction, status: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message(
            "Access denied. CEO credentials required.",
            ephemeral=True
        )
        return

    global ceo_status
    ceo_status = status
    await interaction.response.send_message(
        f"CEO status updated: **{status}**",
        ephemeral=True
    )
    logger.info(f"CEO status set to: {status}")


@bot.tree.command(name="broadcast", description="[CEO] Broadcast a message to Company Bulletins.")
@app_commands.describe(message="The message to broadcast publicly.")
async def cmd_broadcast(interaction: discord.Interaction, message: str):
    if interaction.user.id != CEO_DISCORD_ID:
        await interaction.response.send_message(
            "Access denied. CEO credentials required.",
            ephemeral=True
        )
        return

    channel = bot.get_channel(CHANNEL_BULLETINS)
    if channel is None:
        await interaction.response.send_message(
            "Error: Bulletins channel not found.",
            ephemeral=True
        )
        return

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await channel.send(
        f"**NORTHCORP BULLETIN** — {timestamp}\n\n{message}"
    )
    await interaction.response.send_message(
        "Bulletin transmitted.",
        ephemeral=True
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Standby. Command on cooldown — retry in {error.retry_after:.0f}s.",
            ephemeral=True
        )
    else:
        logger.error(f"Command error: {error}")
        await interaction.response.send_message(
            "Internal error. Notify CEO North.",
            ephemeral=True
        )


threading.Thread(target=start_health_server, daemon=True).start()

if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN not found in .env file.")
        exit(1)

    bot.run(TOKEN)
