import os
import discord
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp
from dotenv import load_dotenv
import random
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME', 'cf_bot_db')

# CF API Base URL
CF_API_URL = "https://codeforces.com/api/"

class CFBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.db_client = None
        self.db = None
        self.session = None

    async def setup_hook(self):
        # Initialize MongoDB
        self.db_client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.db_client[DB_NAME]
        print("[LOG] Connected to MongoDB.")

        # Initialize HTTP session for CF API
        self.session = aiohttp.ClientSession()
        print("[LOG] HTTP session initialized.")

        # Sync slash commands
        await self.tree.sync()
        print("[LOG] Slash commands synced.")

        # Start the submission poller
        self.submission_poller.start()
        print("[LOG] Submission poller started.")

    async def close(self):
        if self.session:
            await self.session.close()
        if self.db_client:
            self.db_client.close()
        await super().close()

    async def on_ready(self):
        print(f'[LOG] Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    @tasks.loop(minutes=2)
    async def submission_poller(self):
        """Background task to check if users have solved their assigned problems."""
        print("[LOG] Polling for solved problems...")

        now = datetime.utcnow()

        # Find all active pending sessions
        async for session in self.db.active_sessions.find({"status": "pending"}):
            # Check for expiration (1 hour)
            start_time = session['start_time']
            if now > start_time + timedelta(hours=1):
                await self.db.active_sessions.update_one(
                    {"_id": session['_id']},
                    {"$set": {"status": "expired"}}
                )
                print(f"[LOG] Session {session['_id']} expired for user {session['user_id']}")
                continue

            user_id = session['user_id']
            problem_id = session['problem_id']

            user_record = await self.db.users.find_one({"discord_id": user_id})
            if not user_record:
                continue

            handle = user_record['cf_handle']
            res = await fetch_cf_api("user.status", {"handle": handle})
            if "error" in res or not res:
                continue

            try:
                contest_id, problem_index = problem_id.split('-')
            except ValueError:
                continue

            solved, attempts = self.check_if_solved(res, contest_id, problem_index)

            if solved:
                await self.mark_as_solved(session, attempts)

    def check_if_solved(self, submissions, contest_id, problem_index):
        """Helper to check if a problem was solved and count attempts."""
        solved = False
        attempts = 0
        for submission in submissions:
            if str(submission['problem']['contestId']) == contest_id and \
               submission['problem']['index'] == problem_index:
                attempts += 1
                if submission['verdict'] == 'OK':
                    solved = True
                    break
        return solved, attempts

    async def mark_as_solved(self, session, attempts):
        """Marks session as completed and sends Discord notification."""
        await self.db.active_sessions.update_one(
            {"_id": session['_id']},
            {"$set": {"status": "completed", "completed_at": datetime.utcnow()}}
        )

        start_time = session['start_time']
        end_time = datetime.utcnow()
        duration = end_time - start_time
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        channel = self.get_channel(session['channel_id'])
        if channel:
            member = channel.guild.get_member(session['user_id'])
            member_name = member.display_name if member else "Someone"

            try:
                contest_id, problem_index = session['problem_id'].split('-')
                link = f"https://codeforces.com/problemset/problem/{contest_id}/{problem_index}"
            except ValueError:
                link = "Unknown Link"

            msg = (f"🎉 **{member_name}** solved the problem!\n"
                   f"⏱️ **Time:** {hours}h {minutes}m {seconds}s\n"
                   f"🎯 **Attempts:** {attempts}\n"
                   f"🔗 {link}")
            await channel.send(msg)

        print(f"[LOG] User {session['user_id']} solved {session['problem_id']}")

bot = CFBot()

async def fetch_cf_api(endpoint, params=None):
    async with bot.session.get(f"{CF_API_URL}{endpoint}", params=params) as resp:
        if resp.status == 200:
            data = await resp.json()
            if data['status'] == 'OK':
                return data['result']
            else:
                return {"error": data.get('comment', 'CF API Error')}
        return {"error": "HTTP Error"}

@bot.tree.command(name="register", description="Register your Codeforces handle")
async def register(interaction: discord.Interaction, handle: str):
    await interaction.response.defer()
    res = await fetch_cf_api("user.info", {"handles": handle})
    if "error" in res:
        await interaction.followup.send(f"❌ Error: {res['error']}")
        return
    if not res or len(res) == 0:
        await interaction.followup.send("❌ Handle not found on Codeforces.")
        return
    user_data = res[0]
    cf_handle = user_data['handle']
    await bot.db.users.update_one(
        {"discord_id": interaction.user.id},
        {"$set": {"cf_handle": cf_handle, "discord_name": interaction.user.name}},
        upsert=True
    )
    await interaction.followup.send(f"✅ Registered successfully! Your handle: **{cf_handle}**")
    print(f"[LOG] User {interaction.user.name} registered with handle {cf_handle}")

@bot.tree.command(name="profile", description="View your Codeforces profile")
async def profile(interaction: discord.Interaction):
    await interaction.response.defer()
    user = await bot.db.users.find_one({"discord_id": interaction.user.id})
    if not user:
        await interaction.followup.send("❌ You are not registered. Use `/register <handle>` first.")
        return
    handle = user['cf_handle']
    res = await fetch_cf_api("user.info", {"handles": handle})
    if "error" in res or not res:
        await interaction.followup.send("❌ Could not fetch profile from Codeforces.")
        return
    data = res[0]
    rating = data.get('rating', 'Unrated')
    rank = data.get('rank', 'No Rank')
    embed = discord.Embed(title=f"Codeforces Profile: {handle}", color=discord.Color.blue())
    embed.add_field(name="Rating", value=str(rating), inline=True)
    embed.add_field(name="Rank", value=str(rank), inline=True)
    embed.set_thumbnail(url=data.get('imageUrl'))
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="randomquestion", description="Get a random Codeforces problem to solve")
async def randomquestion(interaction: discord.Interaction, min_rating: int = 800, max_rating: int = 1200):
    await interaction.response.defer()
    user = await bot.db.users.find_one({"discord_id": interaction.user.id})
    if not user:
        await interaction.followup.send("❌ You must `/register` first!")
        return
    res = await fetch_cf_api("problemset.problems")
    if "error" in res:
        await interaction.followup.send("❌ Failed to fetch problems from Codeforces.")
        return
    problems = res.get('problems', [])
    filtered = [p for p in problems if p.get('rating') and min_rating <= p['rating'] <= max_rating]
    if not filtered:
        await interaction.followup.send("❌ No problems found in that rating range.")
        return
    prob = random.choice(filtered)
    contest_id = prob['contestId']
    index = prob['index']
    problem_id = f"{contest_id}-{index}"
    await bot.db.active_sessions.update_one(
        {"user_id": interaction.user.id},
        {"$set": {
            "problem_id": problem_id,
            "start_time": datetime.utcnow(),
            "status": "pending",
            "channel_id": interaction.channel_id
        }},
        upsert=True
    )
    link = f"https://codeforces.com/problemset/problem/{contest_id}/{index}"
    await interaction.followup.send(f"🎯 **Your Problem:**\nRating: `{prob['rating']}`\nLink: {link}\n\nI'll notify the group once you solve it! Good luck! 🚀")
    print(f"[LOG] Assigned {problem_id} to {user['cf_handle']}")

@bot.tree.command(name="check", description="Manually check if you have solved your assigned problem")
async def check(interaction: discord.Interaction):
    """Manually triggers a status check for the user's current active problem."""
    await interaction.response.defer()

    # Find active session
    session = await bot.db.active_sessions.find_one({"user_id": interaction.user.id})
    if not session:
        await interaction.followup.send("❌ You don't have an active problem. Use `/randomquestion` first!")
        return

    # If the session is already completed, just inform the user
    if session['status'] == 'completed':
        await interaction.followup.send("✅ This problem has already been solved and announced!")
        return

    # If session is expired, it can still be manually checked
    status_prefix = ""
    if session['status'] == 'expired':
        status_prefix = "⚠️ Your session had expired, but let's check anyway... "

    # Get CF handle
    user_record = await bot.db.users.find_one({"discord_id": interaction.user.id})
    if not user_record:
        await interaction.followup.send("❌ You are not registered. Use `/register <handle>` first.")
        return
    handle = user_record['cf_handle']

    # Check CF Status
    res = await fetch_cf_api("user.status", {"handle": handle})
    if "error" in res or not res:
        await interaction.followup.send("❌ Could not fetch your status from Codeforces.")
        return

    # Verify solve
    try:
        contest_id, problem_index = session['problem_id'].split('-')
    except ValueError:
        await interaction.followup.send("❌ Problem ID format error.")
        return

    solved, attempts = bot.check_if_solved(res, contest_id, problem_index)

    if solved:
        await bot.mark_as_solved(session, attempts)
        await interaction.followup.send(f"{status_prefix}🎉 I've detected your solve! Check the channel for the announcement! 🚀")
    else:
        if attempts == 0:
            await interaction.followup.send(f"{status_prefix}❌ You haven't made any submissions for this problem yet.")
        else:
            await interaction.followup.send(f"{status_prefix}❌ Not solved yet. You've made {attempts} attempts. Keep trying! 💪")

@bot.tree.command(name="skip", description="Give up on the current problem and clear your session")
async def skip(interaction: discord.Interaction):
    """Allows the user to stop tracking the current problem."""
    await interaction.response.defer()

    # Find active session
    session = await bot.db.active_sessions.find_one({"user_id": interaction.user.id})
    if not session:
        await interaction.followup.send("❌ You don't have an active problem to skip!")
        return

    if session['status'] == 'completed':
        await interaction.followup.send("✅ This problem was already solved!")
        return

    # Mark as skipped (or just delete the document)
    await bot.db.active_sessions.update_one(
        {"_id": session['_id']},
        {"$set": {"status": "skipped"}}
    )

    await interaction.followup.send("🗑️ Problem skipped! You can now use `/randomquestion` to get a new one.")
    print(f"[LOG] User {interaction.user.id} skipped problem {session['problem_id']}")

if __name__ == "__main__":
    bot.run(TOKEN)
