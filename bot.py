import os
import discord
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient
import aiohttp
from dotenv import load_dotenv
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
        self.scheduler = AsyncIOScheduler()

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

        # Setup Scheduled Tasks
        self.scheduler.add_job(self.daily_question_job, 'cron', hour=9, minute=0) # 9 AM daily
        self.scheduler.add_job(self.contest_notification_job, 'interval', minutes=60)
        self.scheduler.start()
        print("[LOG] Scheduler started (Daily Q & Contest Alerts).")

    async def close(self):
        self.scheduler.shutdown()
        if self.session:
            await self.session.close()
        if self.db_client:
            self.db_client.close()
        await super().close()

    async def on_ready(self):
        print(f'[LOG] Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def daily_question_job(self):
        """Post a random problem to a designated channel."""
        for guild in self.guilds:
            channel = discord.utils.get(guild.text_channels, name='general')
            if not channel: continue

            res = await fetch_cf_api("problemset.problems")
            if "error" in res: return

            problems = res.get('problems', [])
            filtered = [p for p in problems if p.get('rating') and 800 <= p['rating'] <= 1600]
            if not filtered: return
            prob = random.choice(filtered)
            link = f"https://codeforces.com/problemset/problem/{prob['contestId']}/{prob['index']}"

            await channel.send(f"🌅 **Question of the Day!**\nRating: `{prob['rating']}`\nLink: {link}\nGood luck everyone! 🚀")
            print("[LOG] Daily question posted.")

    async def contest_notification_job(self):
        """Notify users about upcoming contests."""
        res = await fetch_cf_api("contest.list")
        if "error" in res: return

        upcoming = [c for c in res if c['status'] == 'REGISTRATION']
        if not upcoming: return

        msg = "📅 **Upcoming Codeforces Contests:**\n"
        for c in upcoming[:5]:
            msg += f"- {c['name']} (Starts: {datetime.fromtimestamp(c['startTime/sec']).strftime('%Y-%m-%d %H:%M')})\n"

        for guild in self.guilds:
            channel = discord.utils.get(guild.text_channels, name='general')
            if channel:
                await channel.send(msg)
        print("[LOG] Contest notifications sent.")

    @tasks.loop(minutes=2)
    async def submission_poller(self):
        """Background task to check if users have solved their assigned problems."""
        print("[LOG] Polling for solved problems...")
        now = datetime.utcnow()

        # 1. Check Practice Sessions
        async for session in self.db.active_sessions.find({"status": "pending"}):
            start_time = session['start_time']
            if now > start_time + timedelta(hours=1):
                # DELETE to keep DB lean
                await self.db.active_sessions.delete_one({"_id": session['_id']})
                print(f"[LOG] Deleted expired session {session['_id']}")
                continue

            user_id = session['user_id']
            problem_id = session['problem_id']
            user_record = await self.db.users.find_one({"discord_id": user_id})
            if not user_record: continue

            res = await fetch_cf_api("user.status", {"handle": user_record['cf_handle']})
            if "error" in res or not res: continue

            try:
                contest_id, problem_index = problem_id.split('-')
                solved, attempts = self.check_if_solved(res, contest_id, problem_index)
                if solved:
                    await self.mark_as_solved(session, attempts)
            except ValueError: continue

        # 2. Check Active Contests/Duels
        async for contest in self.db.contests.find({"status": "active"}):
            if now > contest['start_time'] + timedelta(minutes=contest['duration']):
                await self.end_contest(contest)
                continue

            for p in contest['participants']:
                user_id = p['user_id']
                user_record = await self.db.users.find_one({"discord_id": user_id})
                if not user_record: continue

                res = await fetch_cf_api("user.status", {"handle": user_record['cf_handle']})
                if "error" in res or not res: continue

                for prob_id in contest['problems']:
                    if prob_id in p.get('solved', []): continue

                    try:
                        cid, idx = prob_id.split('-')
                        solved, attempts = self.check_if_solved(res, cid, idx)
                        if solved:
                            await self.db.contests.update_one(
                                {"_id": contest['_id'], "participants.user_id": user_id},
                                {"$push": {"participants.$.solved": prob_id}}
                            )
                            channel = self.get_channel(contest['channel_id'])
                            if channel:
                                member = channel.guild.get_member(user_id)
                                name = member.display_name if member else "User"
                                await channel.send(f"⚡ **{name}** solved `{prob_id}` in contest `{contest['contest_id']}`!")
                    except ValueError: continue

    def check_if_solved(self, submissions, contest_id, problem_index):
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
        # Archive to history (Persistent Store)
        history_entry = {
            "user_id": session['user_id'],
            "problem_id": session['problem_id'],
            "solved_at": datetime.utcnow(),
            "attempts": attempts,
            "time_taken_seconds": (datetime.utcnow() - session['start_time']).total_seconds()
        }
        await self.db.user_history.insert_one(history_entry)

        # Delete active session immediately to keep DB lean
        await self.db.active_sessions.delete_one({"_id": session['_id']})

        start_time = session['start_time']
        duration = datetime.utcnow() - start_time
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        channel = self.get_channel(session['channel_id'])
        if channel:
            member = channel.guild.get_member(session['user_id'])
            name = member.display_name if member else "Someone"
            cid, idx = session['problem_id'].split('-')
            await channel.send(f"🎉 **{name}** solved the problem!\n⏱️ **Time:** {hours}h {minutes}m {seconds}s\n🎯 **Attempts:** {attempts}\n🔗 [Link](https://codeforces.com/problemset/problem/{cid}/{idx})")

    async def end_contest(self, contest):
        # Archive contest results then delete the active contest doc
        updated_contest = await self.db.contests.find_one({"_id": contest['_id']})
        await self.db.contest_history.insert_one({
            "contest_id": contest['contest_id'],
            "type": contest['type'],
            "problems": contest['problems'],
            "participants": updated_contest['participants'],
            "finished_at": datetime.utcnow()
        })
        await self.db.contests.delete_one({"_id": contest['_id']})
        channel = self.get_channel(contest['channel_id'])
        if not channel: return

        best_score = -1
        winner = None
        for p in updated_contest['participants']:
            score = len(p.get('solved', []))
            if score > best_score:
                best_score = score
                winner = p['user_id']

        member = channel.guild.get_member(winner) if winner else None
        winner_name = member.display_name if member else "No one"
        await channel.send(f"🏁 **Contest Finished!**\n🏆 Winner: **{winner_name}** with {best_score} problems solved!")

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
    cid, idx = prob['contestId'], prob['index']
    pid = f"{cid}-{idx}"
    await bot.db.active_sessions.update_one(
        {"user_id": interaction.user.id},
        {"$set": {"problem_id": pid, "start_time": datetime.utcnow(), "status": "pending", "channel_id": interaction.channel_id}},
        upsert=True
    )
    await interaction.followup.send(f"🎯 **Your Problem:**\nRating: `{prob['rating']}`\nLink: https://codeforces.com/problemset/problem/{cid}/{idx}\n\nI'll notify the group once you solve it!")

@bot.tree.command(name="check", description="Manually check if you have solved your assigned problem")
async def check(interaction: discord.Interaction):
    await interaction.response.defer()
    session = await bot.db.active_sessions.find_one({"user_id": interaction.user.id})
    if not session:
        await interaction.followup.send("❌ You don't have an active problem.")
        return
    if session['status'] == 'completed':
        await interaction.followup.send("✅ Already solved!")
        return
    user_record = await bot.db.users.find_one({"discord_id": interaction.user.id})
    res = await fetch_cf_api("user.status", {"handle": user_record['cf_handle']})
    if "error" in res:
        await interaction.followup.send("❌ CF API Error.")
        return
    try:
        cid, idx = session['problem_id'].split('-')
        solved, attempts = bot.check_if_solved(res, cid, idx)
        if solved:
            await bot.mark_as_solved(session, attempts)
            await interaction.followup.send("🎉 Solved! Check the channel.")
        else:
            await interaction.followup.send(f"❌ Not solved yet. Attempts: {attempts}")
    except ValueError:
        await interaction.followup.send("❌ Problem ID error.")

@bot.tree.command(name="skip", description="Give up on the current problem and clear your session")
async def skip(interaction: discord.Interaction):
    await interaction.//db.active_sessions.find_one({"user_id": interaction.user.id})
    # Correction: Use bot.db
    session = await bot.db.active_sessions.find_one({"user_id": interaction.user.id})
    if not session:
        await interaction.followup.send("❌ You don't have an active problem to skip!")
        return
    if session['status'] == 'completed':
        await interaction.followup.send("✅ This problem was already solved!")
        return
    await bot.db.active_sessions.delete_one({"_id": session['_id']})
    await interaction.followup.send("🗑️ Problem skipped! You can now use `/randomquestion` to get a new one.")

@bot.tree.command(name="challenge", description="Challenge a user to a 1v1 Duel")
async def challenge(interaction: discord.Interaction, opponent: discord.Member, num_probs: int = 3, duration_mins: int = 60):
    await interaction.response.defer()
    if opponent == interaction.user:
        await interaction.followup.send("❌ You cannot challenge yourself!")
        return
    user_a = await bot.db.users.find_one({"discord_id": interaction.user.id})
    user_b = await bot.db.users.find_one({"discord_id": opponent.id})
    if not user_a or not user_b:
        await interaction.followup.send("❌ Both users must be registered using `/register` first!")
        return
    res = await fetch_cf_api("user.info", {"handles": [user_a['cf_handle'], user_b['cf_handle']]})
    avg_rating = sum(u.get('rating', 1200) for u in res) / 2
    prob_set = await fetch_cf_api("problemset.problems")
    problems = prob_set.get('problems', [])
    filtered = [p for p in problems if p.get('rating') and abs(p['rating'] - avg_rating) <= 200]
    if len(filtered) < num_probs:
        await interaction.followup.send("❌ Not enough problems found for this rating range.")
        return
    selected = sorted(random.sample(filtered, num_probs), key=lambda x: x['rating'])
    prob_ids = [f"{p['contestId']}-{p['index']}" for p in selected]
    contest_id = f"duel_{interaction.user.id}_{opponent.id}_{int(datetime.utcnow().timestamp())}"
    await bot.db.contests.update_one(
        {"contest_id": contest_id},
        {"$set": {
            "creator_id": interaction.user.id,
            "type": "duel",
            "problems": prob_ids,
            "duration": duration_mins,
            "status": "waiting",
            "participants": [
                {"user_id": interaction.user.id, "solved": []},
                {"user_id": opponent.id, "solved": []}
            ],
            "channel_id": interaction.channel_id
        }},
        upsert=True
    )
    await interaction.followup.send(f"🥊 **Challenge Issued!**\n{opponent.mention}, you've been challenged by {interaction.user.mention}!\n{num_probs} Problems | {duration_mins} Mins.\n\nAdmin: {interaction.user.mention}, use `/startcontest {contest_id}` to begin!")

@bot.tree.command(name="createcontest", description="Create a group contest")
async def createcontest(interaction: discord.Interaction, num_probs: int = 3, duration_mins: int = 60):
    await interaction.response.defer()
    user = await bot.db.users.find_one({"discord_id": interaction.user.id})
    if not user:
        await interaction.followup.send("❌ Register first!")
        return
    res = await fetch_cf_api("user.info", {"handles": user['cf_handle']})
    rating = res[0].get('rating', 1200)
    prob_set = await fetch_cf_api("problemset.problems")
    problems = prob_set.get('problems', [])
    filtered = [p for p in problems if p.get('rating') and abs(p['rating'] - rating) <= 200]
    if len(filtered) < num_probs:
        await interaction.followup.send("❌ Not enough problems found.")
        return
    selected = sorted(random.sample(filtered, num_probs), key=lambda x: x['rating'])
    prob_ids = [f"{p['contestId']}-{p['index']}" for p in selected]
    contest_id = f"contest_{interaction.user.id}_{int(datetime.utcnow().timestamp())}"
    await bot.db.contests.update_one(
        {"contest_id": contest_id},
        {"$set": {
            "creator_id": interaction.user.id,
            "type": "group",
            "problems": prob_ids,
            "duration": duration_mins,
            "status": "waiting",
            "participants": [{"user_id": interaction.user.id, "solved": []}],
            "channel_id": interaction.channel_id
        }},
        upsert=True
    )
    await interaction.followup.send(f"🏆 **Group Contest Created!**\nID: `{contest_id}`\n{num_probs} Problems | {duration_mins} Mins.\n\nOthers can join using `/joincontest {contest_id}`.\nAdmin: Use `/startcontest {contest_id}` to start!")

@bot.tree.command(name="joincontest", description="Join a waiting contest")
async def joincontest(interaction: discord.Interaction, contest_id: str):
    await interaction.response.defer()
    contest = await bot.db.contests.find_one({"contest_id": contest_id})
    if not contest:
        await interaction.followup.send("❌ Contest not found.")
        return
    if contest['status'] != 'waiting':
        await interaction.followup.send("❌ Contest is already active or finished.")
        return
    if any(p['user_id'] == interaction.user.id for p in contest['participants']):
        await interaction.followup.send("❌ You already joined this contest!")
        return
    await bot.db.contests.update_one(
        {"contest_id": contest_id},
        {"$push": {"participants": {"user_id": interaction.user.id, "solved": []}}}
    )
    await interaction.followup.send(f"✅ Joined contest `{contest_id}`! Waiting for admin to start.")

@bot.tree.command(name="startcontest", description="Start a waiting contest (Admin only)")
async def startcontest(interaction: discord.Interaction, contest_id: str):
    await interaction.response.defer()
    contest = await bot.db.contests.find_one({"contest_id": contest_id})
    if not contest:
        await interaction.followup.send("❌ Contest not found.")
        return
    if contest['creator_id'] != interaction.user.id:
        await interaction.followup.send("❌ Only the creator can start the contest!")
        return
    if contest['status'] != 'waiting':
        await interaction.followup.send("❌ Contest is not in waiting state.")
        return
    if len(contest['participants']) < 2:
        await interaction.followup.send("❌ Not enough participants to start (min 2).")
        return
    await bot.db.contests.update_one(
        {"contest_id": contest_id},
        {"$set": {"status": "active", "start_time": datetime.utcnow()}}
    )
    prob_links = "\n".join([f"🔗 [Problem {i+1}](https://codeforces.com/problemset/problem/{p.split('-')[0]}/{p.split('-')[1]})" for i, p in enumerate(contest['problems'])])
    await interaction.followup.send(f"🚀 **The Contest has STARTED!**\nDuration: {contest['duration']} mins\n\n**Problems:**\n{prob_links}\n\nGood luck everyone!")
    print(f"[LOG] Contest {contest_id} started by {interaction.user.name}")

if __name__ == "__main__":
    bot.run(TOKEN)
