import discord
from discord.ext import commands
import sqlite3
import asyncio
from discord import app_commands
from typing import Optional, Tuple, List, Dict
import math
from datetime import datetime, timedelta, timezone
from discord import PartialEmoji
import zoneinfo # Added zoneinfo for robust timezone handling
from discord.ext.commands import BucketType

# Define a type hint for the in-memory usage data
# Key: (user_id, guild_id)
# Value: { 'last_used': float (UTC timestamp), 'daily_count': int, 'current_date': str (EST/EDT YYYY-MM-DD) }
UserUsageData = Dict[Tuple[int, int], Dict[str, any]]

class ReputationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "reputation.db"
        self.init_database()
        self.tree = bot.tree  # Reference to the global app command tree
        self.reaction_rep_tracker = {}  # Fixed: removed 'python' prefix
        self.last_active = {}  # Track user activity for inactivity decay
        self.repeated_messages = {} # Stores last message and timestamp for spam detection
        #self.consecutive_rep_tracker = {}  # Initialize consecutive rep tracker  <-- REMOVE THIS

        # Track consecutive ups/downs *for the receiver*
        self.user_consecutive_tracker: Dict[Tuple[int, int], Dict[str, any]] = {}

        # In-memory storage for command usage cooldowns and daily limits
        # This data WILL be lost if the bot restarts.
        self.user_usage_data: UserUsageData = {}

        # Define reputation tiers and their impact values
        self.positive_tiers = [
            (0, 400, "Lurker", 10),
            (401, 1400, "Anon", 15),
            (1401, 5000, "Based Poster", 20),
            (5001, 10000, "High Quality", 30),
            (10001, float('inf'), "God Poster", 50)
        ]

        self.negative_tiers = [
            (-400, -1, "Newfag", 10),
            (-1400, -401, "Cringe", 5),
            (-5000, -1401, "Thread Derailer", 3),
            (-10000, -5001, "Low Quality", 2),
            (float('-inf'), -10001, "Jannybait", 1)
        ]

        # NEW: Voice Channel Tracking
        self.voice_join_times: Dict[Tuple[int, int], datetime] = {}  # Store user join times in VC (user_id, guild_id): datetime

    def get_consecutive_multiplier(self, receiver_id: int, guild_id: int, is_increase: bool) -> Tuple[float, int]:
        """
        Calculate the consecutive multiplier for reputation changes *for a specific receiver*.
        Returns (multiplier, consecutive_count) tuple.
        """
        key = (receiver_id, guild_id)
        current_time = datetime.utcnow()

        # Get existing tracking data
        tracker = self.user_consecutive_tracker.get(key)

        if not tracker:
            # First time interaction for this receiver
            self.user_consecutive_tracker[key] = {
                'last_direction': is_increase,
                'consecutive_count': 1,
                'last_used': current_time
            }
            return 1.0, 1

        # Check if the direction changed
        if tracker['last_direction'] != is_increase:
            # Direction changed, reset to 1
            self.user_consecutive_tracker[key] = {
                'last_direction': is_increase,
                'consecutive_count': 1,
                'last_used': current_time
            }
            return 1.0, 1

        # Same direction, increment consecutive count
        consecutive_count = tracker['consecutive_count'] + 1
        multiplier = 1.0 + (consecutive_count - 1) * 0.1  # 1.0, 1.1, 1.2, 1.3, etc.

        multiplier = min(multiplier, 3.0) # Cap at 3.0

        # Update tracker
        self.user_consecutive_tracker[key] = {
            'last_direction': is_increase,
            'consecutive_count': consecutive_count,
            'last_used': current_time
        }

        return multiplier, consecutive_count

    async def handle_rep_change(
        self,
        giver: discord.Member,
        receiver: discord.Member,
        guild: discord.Guild,
        increase: bool,
        interaction_or_ctx
    ):
        if giver.id == receiver.id:
            msg = "You can't give reputation to yourself!" if increase else "You can't lower your own reputation!"
            await self._respond(interaction_or_ctx, msg)
            return

        if receiver.bot:
            await self._respond(interaction_or_ctx, "You can't give reputation to bots!")
            return

        allowed, reason = self.can_use_rep_command(giver.id, guild.id)
        if not allowed:
            await self._respond(interaction_or_ctx, reason) # Check rate limits BEFORE doing anything else
            return

        author_rep = self.get_user_rep(giver.id, guild.id)
        author_tier, base_impact = self.get_tier_info(author_rep)

        # Track original base impact for display
        original_base_impact = base_impact

        # Check if user is a booster and apply multiplier
        is_booster = self.has_booster_role(giver)
        booster_multiplier = 2.0 if is_booster else 1.0

        if is_booster:
            base_impact *= 2

        if author_tier == "Jannybait":
            await self._respond(interaction_or_ctx, "You can't affect others' scores.")
            return

        # Get consecutive multiplier (using the *receiver's* history)
        consecutive_multiplier, consecutive_count = self.get_consecutive_multiplier(receiver.id, guild.id, increase)

        # Apply consecutive multiplier and round to nearest whole number
        final_impact = round(base_impact * consecutive_multiplier)

        self.update_rep_usage(giver.id, guild.id)
        delta = final_impact if increase else -final_impact
        self.adjust_rep(receiver.id, guild.id, delta)

        # Create detailed embed
        embed = discord.Embed(
            title="Reputation Changed",
            description=(
                f"{receiver.mention} {'received' if increase else 'lost'} **{abs(delta)}** reputation "
                f"from {giver.mention}"
            ),
            color=0x00ff00 if increase else 0xff0000
        )

        # Add detailed breakdown
        breakdown_text = f"**Base Impact:** {original_base_impact} ({author_tier})\n"

        if is_booster:
            breakdown_text += f"**Booster Bonus:** ×{booster_multiplier} → {original_base_impact * booster_multiplier}\n"

        # REMOVE THIS SECTION FROM HERE
        #if consecutive_count > 1:
        #    breakdown_text += f"**Consecutive Bonus:** �{consecutive_multiplier:.1f} (#{consecutive_count} consecutive {'up' if increase else 'down'})\n"
        #    breakdown_text += f"**Final Calculation:** {base_impact} � {consecutive_multiplier:.1f} = {final_impact}\n"

        breakdown_text += f"**Applied Change:** {'+' if increase else '-'}{abs(delta)}"

        embed.add_field(name="Calculation Breakdown", value=breakdown_text, inline=False)

        new_rep = self.get_user_rep(receiver.id, guild.id)
        new_tier, _ = self.get_tier_info(new_rep)
        embed.add_field(name="New Score", value=f"{new_rep} ({new_tier})", inline=False)

        await self._respond(interaction_or_ctx, embed=embed)

    async def _respond(self, ctx_or_interaction, content=None, embed=None):
        if isinstance(ctx_or_interaction, discord.Interaction):
            if ctx_or_interaction.response.is_done():
                await ctx_or_interaction.followup.send(content=content, embed=embed, ephemeral=True)
            else:
                await ctx_or_interaction.response.send_message(content=content, embed=embed, ephemeral=True)
        else:
            await ctx_or_interaction.send(content=content, embed=embed, delete_after=30)

    async def inactivity_decay_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            now_est = self.get_current_est_datetime()
            next_run = now_est.replace(hour=0, minute=1, second=0, microsecond=0)
            if next_run < now_est:
                next_run += timedelta(days=1)

            wait_seconds = (next_run - now_est).total_seconds()
            await asyncio.sleep(wait_seconds)

            print(f"[Inactivity Decay] Running daily check at {now_est}")

            for guild in self.bot.guilds:
                for member in guild.members:
                    if member.bot:
                        continue

                    user_id = member.id
                    guild_id = guild.id
                    last_seen = self.last_active.get(user_id)

                    if not last_seen:
                        continue  # No activity record

                    days_inactive = (datetime.utcnow() - last_seen).days
                    if days_inactive < 1:
                        continue  # Still active

                    current_rep = self.get_user_rep(user_id, guild_id)

                    if current_rep <= 0:
                        continue  # No rep to lose

                    # Calculate how much to remove (doubling, capped at 50)
                    penalty = 0
                    for day in range(1, days_inactive + 1):
                        step = 5 * (2 ** (day - 1))
                        if penalty + step > 50:
                            step = 50 - penalty
                        penalty += step
                        if penalty >= 50:
                            break

                    penalty = min(penalty, current_rep)
                    self.adjust_rep(user_id, guild_id, -penalty)

    def init_database(self):
        """Initializes the SQLite database tables if they don't exist.
        Only 'reputation' and 'rep_passive' tables are managed here now."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reputation (
                user_id INTEGER,
                guild_id INTEGER,
                reputation INTEGER DEFAULT 0,
                UNIQUE(user_id, guild_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rep_passive (
                user_id INTEGER,
                guild_id INTEGER,
                hour INTEGER,           -- Actual EST/EDT hour when rep was gained
                date TEXT,              -- EST/EDT date when rep was gained
                PRIMARY KEY (user_id, guild_id, hour, date)
            )
        ''')
        conn.commit()
        conn.close()

    @staticmethod
    def get_vc_rep_gain(hour: int) -> int:
        """Calculate positive rep gain for VC time at given hour (1 to 8)."""
        if hour < 1 or hour > 8:
            return 0
        min_rep, max_rep = 2, 10
        rep = min_rep + (max_rep - min_rep) * (hour - 1) / 7
        return round(rep)

    @staticmethod
    def get_vc_deafened_rep_loss(hour: int) -> int:
        """Calculate negative rep loss for VC deafened time at given hour (1 to 8)."""
        if hour < 1 or hour > 8:
            return 0
        min_rep, max_rep = -1, -5
        rep = min_rep + (max_rep - min_rep) * (hour - 1) / 7
        return round(rep)

    def has_low_quality_role(self, member: discord.Member) -> bool:
        return any(role.name == "Low Quality" for role in member.roles)

    def get_user_rep(self, user_id: int, guild_id: int) -> int:
        """Retrieves a user's reputation score for a specific guild."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT reputation FROM reputation WHERE user_id = ? AND guild_id = ?', (user_id, guild_id))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0

    def set_user_rep(self, user_id: int, guild_id: int, rep: int):
        """Sets or updates a user's reputation score."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO reputation (user_id, guild_id, reputation)
            VALUES (?, ?, ?)
        ''', (user_id, guild_id, rep))
        conn.commit()
        conn.close()

    def get_all_server_reps(self, guild_id: int) -> List[Tuple[int, int]]:
        """Retrieves all user reputation scores for a given guild."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, reputation FROM reputation WHERE guild_id = ?', (guild_id,))
        results = cursor.fetchall()
        conn.close()
        return results

    def get_tier_info(self, rep: int) -> Tuple[str, int]:
        """Determines the reputation tier and its impact value based on a score."""
        for min_rep, max_rep, title, impact in self.positive_tiers:
            if min_rep <= rep <= max_rep:
                return title, impact
        for min_rep, max_rep, title, impact in self.negative_tiers:
            if min_rep <= rep <= max_rep:
                return title, impact
        return "Unknown", 0 # Default if no tier matches (shouldn't happen with proper ranges)

    # --- Robust Timezone Helpers (Using zoneinfo) ---
    def get_est_timezone_obj(self):
        """Returns the ZoneInfo object for America/New_York (EST/EDT)."""
        # 'America/New_York' handles daylight saving automatically
        return zoneinfo.ZoneInfo("America/New_York")

    def get_current_est_datetime(self):
        """Returns the current datetime in EST/EDT."""
        return datetime.now(self.get_est_timezone_obj())

    def get_current_est_date(self) -> str:
        """Returns the current date in EST/EDT as 'YYYY-MM-DD'."""
        return self.get_current_est_datetime().strftime('%Y-%m-%d')

    # --- Command Cooldown and Daily Limit Logic (IN-MEMORY) ---
    def can_use_rep_command(self, user_id: int, guild_id: int) -> Tuple[bool, str]:
        """
        Checks if a user can use a reputation command based on daily limit and cooldown.
        Uses in-memory storage.
        Returns (True, "") if allowed, or (False, reason_string) if not.
        """
        user_key = (user_id, guild_id)
        current_est_date = self.get_current_est_date()
        current_utc_timestamp = datetime.now(timezone.utc).timestamp()

        # Retrieve user's usage data from in-memory dictionary
        usage_record = self.user_usage_data.get(user_key)

        if not usage_record:
            # No record found, user can use the command
            return True, ""

        last_used_timestamp = usage_record['last_used']
        daily_count = usage_record['daily_count']
        stored_date = usage_record['current_date']

        # Check if it's a new day (EST/EDT) for the daily limit
        if stored_date != current_est_date:
            # New day, daily count will be reset, and cooldown is implicitly reset
            return True, ""

        # If it's the same day, check the daily limit
        if daily_count >= 10:
            return False, "You've reached your daily limit of 10 reputation uses. Try again after 00:01 EST."

        # Check the 30-minute cooldown
        time_diff = current_utc_timestamp - last_used_timestamp

        if time_diff < 1800:  # 30 minutes in seconds
            seconds_left = int(1800 - time_diff)
            minutes, seconds = divmod(seconds_left, 60)
            return False, f"You must wait {minutes} minute(s) and {seconds} second(s) before using reputation commands again."

        return True, ""

    def update_rep_usage(self, user_id: int, guild_id: int):
        """Updates user's reputation command usage tracking in in-memory storage."""
        user_key = (user_id, guild_id)
        current_est_date = self.get_current_est_date()
        current_utc_timestamp = datetime.now(timezone.utc).timestamp()

        # Retrieve user's usage data from in-memory dictionary
        usage_record = self.user_usage_data.get(user_key)

        if not usage_record:
            # First time using a rep command for this user in this guild (in this bot session)
            self.user_usage_data[user_key] = {
                'last_used': current_utc_timestamp,
                'daily_count': 1,
                'current_date': current_est_date
            }
        else:
            daily_count = usage_record['daily_count']
            stored_date = usage_record['current_date']

            if stored_date != current_est_date:
                # New day, reset daily count
                new_count = 1
            else:
                # Same day, increment daily count
                new_count = daily_count + 1

            self.user_usage_data[user_key] = {
                'last_used': current_utc_timestamp,
                'daily_count': new_count,
                'current_date': current_est_date
            }

    # --- Passive Reputation Gain Logic (Still uses SQLite) ---
    def get_hourly_rep_gain(self, hour_index: int) -> int:
        """Calculates the passive reputation gain for a given hour index (1-14)."""
        # Rep gain increases from 1 (1st hour) to 25 (14th hour)
        if hour_index < 1 or hour_index > 14:
            return 0
        step = (25 - 1) / 13 # (Max_rep - Min_rep) / (Num_hours - 1)
        rep = 1 + (hour_index - 1) * step
        return round(rep)

    def has_received_hourly_rep(self, user_id: int, guild_id: int, hour: int, date: str) -> bool:
        """Checks if a user has already received passive rep for a specific hour on a given date."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM rep_passive
            WHERE user_id = ? AND guild_id = ? AND hour = ? AND date = ?
        ''', (user_id, guild_id, hour, date))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def log_hourly_rep(self, user_id: int, guild_id: int, hour: int, date: str):
        """Logs that a user has received passive rep for a specific hour on a given date."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO rep_passive (user_id, guild_id, hour, date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, guild_id, hour, date))
        conn.commit()
        conn.close()

    def adjust_rep(self, user_id: int, guild_id: int, delta: int):
        """Adjusts a user's reputation score by a given delta."""
        current_rep = self.get_user_rep(user_id, guild_id)
        new_rep = current_rep + delta
        self.set_user_rep(user_id, guild_id, new_rep)

    def has_booster_role(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    # --- Discord Commands ---
    # Prefix (~) command
    @commands.command(name="rep")
    @commands.cooldown(rate=1, per=60, type=BucketType.user)
    async def rep_command(self, ctx, user: Optional[discord.Member] = None):
        user = user or ctx.author
        await self.display_rep(ctx, user)

    # Slash command
    @app_commands.command(name="rep", description="Check a user's reputation.")
    @app_commands.describe(user="The user to check. Leave blank to check yourself.")
    async def rep_slash(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        user = user or interaction.user
        await self.display_rep(interaction, user)

    async def display_rep(self, ctx_or_interaction, user: discord.Member):
        """Displays a user's reputation, with detailed impact info."""
        user_id = user.id
        guild_id = ctx_or_interaction.guild.id if isinstance(ctx_or_interaction, commands.Context) else ctx_or_interaction.guild_id

        rep = self.get_user_rep(user_id, guild_id)
        tier, base_impact = self.get_tier_info(rep)

        # Determine embed color based on rep
        color = 0x00ff00 if rep > 0 else 0xff0000 if rep < 0 else 0x808080

        is_booster = self.has_booster_role(user)
        booster_multiplier = 2.0 if is_booster else 1.0
        boosted_impact = base_impact * booster_multiplier

        # Get example consecutive multiplier based on past tracking
        # This is approximate because direction (up/down) isn't known here
        max_consecutive = 1.0
        #for key, tracker in self.consecutive_rep_tracker.items():  <-- REMOVE THIS
        #    if key[0] == user_id and key[2] == guild_id:
        #        max_consecutive = max(max_consecutive, 1.0 + (tracker['consecutive_count'] - 1) * 0.1)

        final_impact = round(boosted_impact * max_consecutive)

        embed = discord.Embed(
            title=f"{user.display_name}'s Reputation",
            color=color
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="Score", value=str(rep), inline=True)
        embed.add_field(name="Tier", value=tier, inline=True)
        embed.add_field(name="Raw Rep Power", value=str(base_impact), inline=True)

        boost_info = f"×{booster_multiplier} (Nitro Booster)" if is_booster else "×1 (No boost)"
        embed.add_field(name="Booster Multiplier", value=boost_info, inline=True)

        embed.add_field(
            name="Final Potential Impact",
            value=f"{base_impact} × {booster_multiplier} × {max_consecutive:.1f} = {final_impact}",
            inline=False
        )

        await self._respond(ctx_or_interaction, embed=embed)


    @commands.command(name='repstat')
    @commands.cooldown(rate=1, per=60, type=BucketType.user)
    async def rep_stats(self, ctx, page: int = 1):
        """Display reputation leaderboard with pagination."""
        all_reps = self.get_all_server_reps(ctx.guild.id)

        # Filter out users who are no longer in the server
        server_reps = []
        for user_id, rep in all_reps:
            member = ctx.guild.get_member(user_id)
            if member:
                server_reps.append((member, rep))

        if not server_reps:
            await ctx.send("No reputation data found for this server.", delete_after=30)
            return

        # Sort by reputation (highest to lowest)
        server_reps.sort(key=lambda x: x[1], reverse=True)

        if page == 1:
            # Page 1: Top 5 highest + Top 5 lowest
            embed = discord.Embed(
                title=" Reputation Leaderboard - Overview",
                color=0x4169E1
            )

            # Top 5 highest
            top_5 = server_reps[:5]
            top_text = ""
            for i, (member, rep) in enumerate(top_5, 1):
                tier, _ = self.get_tier_info(rep)
                top_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.add_field(name=" Top 5 Highest", value=top_text or "No data", inline=False)

            # Bottom 5 lowest
            bottom_5 = server_reps[-5:]
            bottom_5.reverse()  # Show lowest first
            bottom_text = ""
            for i, (member, rep) in enumerate(bottom_5, 1):
                tier, _ = self.get_tier_info(rep)
                bottom_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.add_field(name=" Bottom 5 Lowest", value=bottom_text or "No data", inline=False)

            # Calculate total pages for the full list (starting from page 2)
            total_full_list_pages = math.ceil(len(server_reps) / 10)
            total_pages_display = total_full_list_pages + 1 # +1 for the overview page
            embed.set_footer(text=f"Page 1/{total_pages_display} - Use ~repstat <page> for full list")

        else:
            # Pages 2+: Full sorted list (10 per page)
            per_page = 10
            # Adjust start_idx because page 1 is the overview, full list starts from page 2
            start_idx = (page - 2) * per_page
            end_idx = start_idx + per_page
            page_data = server_reps[start_idx:end_idx]

            if not page_data:
                await ctx.send("Page not found! Please enter a valid page number.", delete_after=30)
                return

            embed = discord.Embed(
                title=f" Reputation Leaderboard - Page {page}",
                color=0x4169E1
            )

            leaderboard_text = ""
            for i, (member, rep) in enumerate(page_data, start_idx + 1):
                tier, _ = self.get_tier_info(rep)
                leaderboard_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.description = leaderboard_text

            total_full_list_pages = math.ceil(len(server_reps) / 10)
            total_pages_display = total_full_list_pages + 1
            embed.set_footer(text=f"Page {page}/{total_pages_display}")

        await ctx.send(embed=embed, delete_after=30)

    @app_commands.command(name="repstat", description="View the reputation leaderboard")
    @app_commands.describe(page="Leaderboard page number (1 = top/bottom, 2+ = full list)")
    async def repstat_slash(self, interaction: discord.Interaction, page: int = 1):
        """View the reputation leaderboard using a slash command."""
        await self.rep_stats_interaction(interaction, page)

    async def rep_stats_interaction(self, interaction: discord.Interaction, page: int = 1):
        """Display reputation leaderboard with pagination for interactions."""
        all_reps = self.get_all_server_reps(interaction.guild_id)

        # Filter out users who are no longer in the server
        server_reps = []
        for user_id, rep in all_reps:
            member = interaction.guild.get_member(user_id)
            if member:
                server_reps.append((member, rep))

        if not server_reps:
            await interaction.response.send_message("No reputation data found for this server.", ephemeral=True)
            return

        # Sort by reputation (highest to lowest)
        server_reps.sort(key=lambda x: x[1], reverse=True)

        if page == 1:
            # Page 1: Top 5 highest + Top 5 lowest
            embed = discord.Embed(title=" Reputation Leaderboard - Overview", color=0x4169E1)

            # Top 5 highest
            top_5 = server_reps[:5]
            top_text = ""
            for i, (member, rep) in enumerate(top_5, 1):
                tier, _ = self.get_tier_info(rep)
                top_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.add_field(name=" Top 5 Highest", value=top_text or "No data", inline=False)

            # Bottom 5 lowest
            bottom_5 = server_reps[-5:]
            bottom_5.reverse()  # Show lowest first
            bottom_text = ""
            for i, (member, rep) in enumerate(bottom_5, 1):
                tier, _ = self.get_tier_info(rep)
                bottom_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.add_field(name=" Bottom 5 Lowest", value=bottom_text or "No data", inline=False)

            # Calculate total pages for the full list (starting from page 2)
            total_full_list_pages = math.ceil(len(server_reps) / 10)
            total_pages_display = total_full_list_pages + 1  # +1 for the overview page
            embed.set_footer(text=f"Page 1/{total_pages_display} - Use /repstat <page> for full list")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        else:
            # Pages 2+: Full sorted list (10 per page)
            per_page = 10
            # Adjust start_idx because page 1 is the overview, full list starts from page 2
            start_idx = (page - 2) * per_page
            end_idx = start_idx + per_page
            page_data = server_reps[start_idx:end_idx]

            if not page_data:
                await interaction.response.send_message("Page not found! Please enter a valid page number.", ephemeral=True)
                return

            embed = discord.Embed(title=f" Reputation Leaderboard - Page {page}", color=0x4169E1)

            leaderboard_text = ""
            for i, (member, rep) in enumerate(page_data, start_idx + 1):
                tier, _ = self.get_tier_info(rep)
                leaderboard_text += f"{i}. {member.display_name}: {rep} ({tier})\n"

            embed.description = leaderboard_text

            total_full_list_pages = math.ceil(len(server_reps) / 10)
            total_pages_display = total_full_list_pages + 1
            embed.set_footer(text=f"Page {page}/{total_pages_display}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="up", description="Give a user positive reputation")
    @app_commands.describe(user="The user to upvote")
    async def up_slash(self, interaction: discord.Interaction, user: discord.Member):
        await self.handle_rep_change(interaction.user, user, interaction.guild, increase=True, interaction_or_ctx=interaction)

    @commands.command()
    @commands.cooldown(rate=1, per=60, type=BucketType.user)
    async def up(self, ctx, user: discord.Member):
        """Increases a user's reputation."""
        if ctx.author.id == user.id:
            await ctx.send("You can't give reputation to yourself!", delete_after=30)
            return

        if user.bot:
            await ctx.send("You can't give reputation to bots!", delete_after=30)
            return

        # Check rate limits BEFORE doing anything else
        allowed, reason = self.can_use_rep_command(ctx.author.id, ctx.guild.id)
        if not allowed:
            await ctx.send(reason, delete_after=30)
            return

        # Get the rep impact from the author's current rep tier
        author_rep = self.get_user_rep(ctx.author.id, ctx.guild.id)
        author_tier, impact = self.get_tier_info(author_rep)

        if self.has_booster_role(ctx.author):
            impact *= 2

        if author_tier == "Jannybait": # Check if the author is in the "Jannybait" tier
            await ctx.send("You can't affect others' scores.", delete_after=30)
            return

        # Get consecutive multiplier (using the *receiver's* history)
        consecutive_multiplier, consecutive_count = self.get_consecutive_multiplier(user.id, ctx.guild.id, True)

        # Apply consecutive multiplier and round to nearest whole number
        final_impact = round(impact * consecutive_multiplier)

        # Update usage tracking (in-memory)
        self.update_rep_usage(ctx.author.id, ctx.guild.id)
        self.adjust_rep(user.id, ctx.guild.id, final_impact)  # Use final_impact here

        embed = discord.Embed(
            title=" Reputation Increased",
            description=f"{user.mention} received **+{final_impact}** reputation from {ctx.author.mention}", # Use final_impact here
            color=0x00ff00
        )
        new_rep = self.get_user_rep(user.id, ctx.guild.id)
        new_tier, _ = self.get_tier_info(new_rep)
        embed.add_field(name="New Score", value=f"{new_rep} ({new_tier})", inline=False)

        await ctx.send(embed=embed, delete_after=30)

    @app_commands.command(name="down", description="Lower a user's reputation")
    @app_commands.describe(user="The user to downvote")
    async def down_slash(self, interaction: discord.Interaction, user: discord.Member):
        await self.handle_rep_change(interaction.user, user, interaction.guild, increase=False, interaction_or_ctx=interaction)


    @commands.command()
    @commands.cooldown(rate=1, per=60, type=BucketType.user)
    async def down(self, ctx, user: discord.Member):
        """Decreases a user's reputation."""
        if ctx.author.id == user.id:
            await ctx.send("You can't lower your own reputation!", delete_after=30)
            return

        if user.bot:
            await ctx.send("You can't give reputation to bots!", delete_after=30)
            return

        # Check rate limits BEFORE doing anything else
        allowed, reason = self.can_use_rep_command(ctx.author.id, ctx.guild.id)
        if not allowed:
            await ctx.send(reason, delete_after=30)
            return

        # Get the rep impact from the author's current rep tier
        author_rep = self.get_user_rep(ctx.author.id, ctx.guild.id)
        author_tier, impact = self.get_tier_info(author_rep)

        if self.has_booster_role(ctx.author):
            impact *= 2

        if author_tier == "Jannybait": # Check if the author is in the "Jannybait" tier
            await ctx.send("You can't affect others' scores.", delete_after=30)
            return

        # Get consecutive multiplier (using the *receiver's* history)
        consecutive_multiplier, consecutive_count = self.get_consecutive_multiplier(user.id, ctx.guild.id, False)

        # Apply consecutive multiplier and round to nearest whole number
        final_impact = round(impact * consecutive_multiplier)

        # Update usage tracking (in-memory)
        self.update_rep_usage(ctx.author.id, ctx.guild.id)
        self.adjust_rep(user.id, ctx.guild.id, -final_impact) # Use -final_impact

        embed = discord.Embed(
            title=" Reputation Decreased",
            description=f"{user.mention} lost **-{final_impact}** reputation from {ctx.author.mention}", # Use -final_impact
            color=0xff0000
        )
        new_rep = self.get_user_rep(user.id, ctx.guild.id)
        new_tier, _ = self.get_tier_info(new_rep)
        embed.add_field(name="New Score", value=f"{new_rep} ({new_tier})", inline=False)

        await ctx.send(embed=embed, delete_after=30)

    async def silent_rep_penalty(self, user_id: int, guild_id: int, penalty: int):
        """Applies a silent reputation penalty for repeated messages."""
        self.adjust_rep(user_id, guild_id, -penalty)
        print(f"[Repeat Penalty] User {user_id} in guild {guild_id} lost {penalty} rep for repeated message.")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot or reaction.message.author.bot:
            return

        message = reaction.message
        giver_id = user.id
        receiver_id = message.author.id
        guild_id = message.guild.id

        if giver_id == receiver_id:
            return  # No self-repping

        positive_emojis = {
            "\u2705",  "\U0001f44d",   "\u2764\ufe0f",
            PartialEmoji(name="Real", id=1387895718290915500),
            PartialEmoji(name="based", id=1387864965754654770),
        }

        negative_emojis = {
            "\u274c",  "\U0001f494", "\U0001f44e",
            PartialEmoji(name="cringe", id=1387893075720015892),
            PartialEmoji(name="fake", id=1387895448609489037),
        }

        emoji_obj = reaction.emoji  # This could be str or PartialEmoji

        if emoji_obj not in positive_emojis and emoji_obj not in negative_emojis:
            return

        # EST date check
        current_date = self.get_current_est_date()
        tracker = self.reaction_rep_tracker.get(giver_id)

        if not tracker or tracker["date"] != current_date:
            tracker = {
                "date": current_date,
                "given": set(),
                "taken": set()
            }
            self.reaction_rep_tracker[giver_id] = tracker

        if emoji_obj in positive_emojis:
            if receiver_id in tracker['given'] or len(tracker['given']) >= 5:
                return
            tracker['given'].add(receiver_id)
            self.adjust_rep(receiver_id, guild_id, 1)

        elif emoji_obj in negative_emojis:
            if receiver_id in tracker['taken'] or len(tracker['taken']) >= 5:
                return
            tracker['taken'].add(receiver_id)
            self.adjust_rep(receiver_id, guild_id, -1)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listener for message events to handle passive rep gain and repeat message penalties."""
        if message.author.bot or not message.guild:
            return

        user_id = message.author.id
        guild_id = message.guild.id
        content = message.content.strip()
        now_utc = datetime.utcnow()  # Use UTC for internal timestamp of repeated messages

        # Track user activity for inactivity decay system
        self.last_active[user_id] = now_utc

        # --- Repeated Message Penalty ---
        # Only consider messages longer than 20 characters
        if len(content) > 20 and not self.has_low_quality_role(message.author):
            entry = self.repeated_messages.get(user_id)

            if entry:
                delta = (now_utc - entry["timestamp"]).total_seconds()
                if content == entry["last_message"] and delta <= 60:
                    entry["count"] += 1
                    entry["timestamp"] = now_utc

                    # Start applying penalties from the 4th repeated message
                    if entry["count"] >= 4:
                        penalty = entry["count"] - 3  # 4th message = 1 rep loss, 5th = 2, etc.
                        await self.silent_rep_penalty(user_id, guild_id, penalty)
                else:
                    # Message is different or outside the time window, reset tracking
                    self.repeated_messages[user_id] = {
                        "last_message": content,
                        "timestamp": now_utc,
                        "count": 1
                    }
            else:
                # First message from this user to track
                self.repeated_messages[user_id] = {
                    "last_message": content,
                    "timestamp": now_utc,
                    "count": 1
                }

        # --- Passive Hourly Rep Gain ---
        now_est = self.get_current_est_datetime()
        est_hour = now_est.hour
        current_est_date = self.get_current_est_date()

        # Use a single database connection for both queries
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT COUNT(DISTINCT hour)
                FROM rep_passive
                WHERE user_id = ? AND guild_id = ? AND date = ?
            ''', (user_id, guild_id, current_est_date))
            hour_count = cursor.fetchone()[0]

            # Grant passive rep if less than 14 hours have been logged for the day
            # and the user hasn't received rep for the current EST hour yet.
            if hour_count < 14 and not self.has_received_hourly_rep(user_id, guild_id, est_hour, current_est_date):
                hour_index = hour_count + 1  # This is the (N)th hour they are getting rep for today
                gain = self.get_hourly_rep_gain(hour_index)
                self.adjust_rep(user_id, guild_id, gain)
                self.log_hourly_rep(user_id, guild_id, est_hour, current_est_date)
                # print(f"User {user_id} gained {gain} rep for hour {est_hour} on {current_est_date}. Total hours today: {hour_index}")  # For debugging

        finally:
            conn.close()

    @commands.Cog.listener()
    async def on_ready(self):
        if not hasattr(self, "synced"):
            await self.tree.sync()
            self.synced = True
            print("[Slash Commands Synced]")
        if not hasattr(self, "inactivity_task_started"):
            self.bot.loop.create_task(self.inactivity_decay_loop())
            self.inactivity_task_started = True

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Tracks voice channel activity and awards reputation."""
        user_id = member.id
        guild_id = member.guild.id
        key = (user_id, guild_id)

        # User joins a voice channel
        if before.channel is None and after.channel is not None:
            self.voice_join_times[key] = datetime.utcnow()
            print(f"User {user_id} joined VC in guild {guild_id} at {self.voice_join_times[key]}") # Debugging

        # User leaves a voice channel
        elif before.channel is not None and after.channel is None:
            if key in self.voice_join_times:
                join_time = self.voice_join_times[key]
                del self.voice_join_times[key]
                time_in_vc = datetime.utcnow() - join_time
                minutes_in_vc = time_in_vc.total_seconds() / 60

                # Award reputation (example: 1 rep per 10 minutes)
                rep_gain = int(minutes_in_vc / 10)
                if rep_gain > 0:
                    self.adjust_rep(user_id, guild_id, rep_gain)
                    print(f"User {user_id} gained {rep_gain} rep for being in VC for {minutes_in_vc:.2f} minutes.")

                # Consider deafened/muted status (example)
                if member.voice is not None: # Check if the user is still in a voice channel
                    if member.voice.deaf or member.voice.mute:
                        rep_loss = int(minutes_in_vc / 20) # Less rep if deafened/muted
                        self.adjust_rep(user_id, guild_id, -rep_loss)
                        print(f"User {user_id} lost {rep_loss} rep for being deafened/muted in VC for {minutes_in_vc:.2f} minutes.")
            else:
                print(f"User {user_id} left VC in guild {guild_id}, but no join time was recorded.") # Debugging

async def setup(bot):
    """Sets up the ReputationCog in the bot."""
    await bot.add_cog(ReputationCog(bot)) 
