import os
import discord
import base64
import random
import json
import sqlite3
import asyncio
import requests
import re
from datetime import datetime
from discord.ext import commands
from io import BytesIO

ALLOWED_ROLES = {"☆", "III", "II", "I"}

class Cog1(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "pinned_messages.db"
        self.init_pin_database()
        self.invite_cache = {}
        self.tracked_invite_code = "qnDWXbzywE"
        self.role_name = "lunatic"  # Role to assign
        self.rape_messages = [
            "{author} rapes {target} brutally",
            "{author} and {target} share a passionate rapesesh",
            "{author} sneakily rapes {target}",
            "{author} blasts a nuclear rape missle at {target} from afar",
            "{author} stops time to rape {target}",
            "{author} takes {target} by surprise with a violent rape",
            "{author} uses {target} for their own pleasure, raping them without consent",
            "{author} pins {target} down and rapes them roughly",
            "{author} forces themselves on {target}, taking what they want",
            "{author} rapes {target} in a dark, isolated place",
            "{author} overpowers {target} and rapes them aggressively",
            "{author} takes advantage of {target}'s vulnerability and rapes them",
            "{author} rapes {target} with no regard for their feelings or safety",
            "{author} uses force to rape {target}, leaving them traumatized",
            "{author} rapes {target} repeatedly, causing them immense pain and suffering",
            "{author} rapes {target} at 5013 baldpate drive, corpus christi texas, 78413",
        ]


    # --- Bump Reminder Setup ---
        self.disboard_bot_id = 302050872324835328 # Official Disboard bot ID
        self.my_user_id = 1387430259498156103 # Your user ID for DM reminders
        self.bump_reminder_delay_seconds = 2 * 60 * 60 # 2 hours in seconds
        self.bump_times_file = "disboard_bump_times.json"
        
        self.last_bump_times = {} # Stores {guild_id: datetime_object}
        self.reminder_tasks = {} # Stores {guild_id: asyncio.Task} for active reminders
        self.load_bump_times() # Load previous bump times on startup

    def load_bump_times(self):
        """Loads last bump times from file and reschedules reminders if necessary."""
        try:
            with open(self.bump_times_file, "r") as f:
                loaded_data = json.load(f)
                for guild_id, timestamp_str in loaded_data.items():
                    # Convert string timestamp back to datetime object
                    self.last_bump_times[guild_id] = datetime.fromisoformat(timestamp_str)
                    
                    # Reschedule reminders for bumps that happened less than 2 hours ago
                    time_since_bump = datetime.now() - self.last_bump_times[guild_id]
                    if time_since_bump.total_seconds() < self.bump_reminder_delay_seconds:
                        remaining_delay = self.bump_reminder_delay_seconds - time_since_bump.total_seconds()
                        print(f"Rescheduling bump reminder for guild {guild_id} in {remaining_delay:.0f} seconds.")
                        self.reminder_tasks[guild_id] = asyncio.create_task(
                            self._schedule_bump_reminder(guild_id, remaining_delay)
                        )
                    else:
                        print(f"Bump for guild {guild_id} was too long ago, not rescheduling.")

        except FileNotFoundError:
            self.last_bump_times = {}
            print(f"{self.bump_times_file} not found. Starting with empty bump times.")
        except json.JSONDecodeError:
            print(f"Error decoding {self.bump_times_file}. Starting with empty bump times.")
            self.last_bump_times = {}
            
    def init_pin_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pinned_messages (
                message_id INTEGER PRIMARY KEY,
                webhook_message_id INTEGER
            )
        ''')
        conn.commit()
        conn.close()

    def get_pinned_webhook_message_id(self, message_id: int) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT webhook_message_id FROM pinned_messages WHERE message_id = ?", (message_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def save_pinned_message(self, message_id: int, webhook_message_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO pinned_messages (message_id, webhook_message_id) VALUES (?, ?)",
                       (message_id, webhook_message_id))
        conn.commit()
        conn.close()
        
    async def get_random_gif(self,type="rape"):
        """Gets a random GIF, avoiding recently used ones."""
        if type == "rape":
            gif_url = requests.get("https://api.purrbot.site/v2/img/nsfw/anal/gif",timeout=3).json()["link"]
        elif type == "gay":
            gif_url = requests.get("https://api.purrbot.site/v2/img/nsfw/yaoi/gif",timeout=3).json()["link"]
        elif type == "lesbian":
            gif_url = requests.get("https://api.purrbot.site/v2/img/nsfw/yuri/gif",timeout=3).json()["link"]
        return gif_url

    def save_bump_times(self):
        """Saves current bump times to file."""
        # Convert datetime objects to ISO format strings for JSON serialization
        data_to_save = {
            guild_id: dt_obj.isoformat() 
            for guild_id, dt_obj in self.last_bump_times.items()
        }
        with open(self.bump_times_file, "w") as f:
            json.dump(data_to_save, f, indent=4)

    async def _schedule_bump_reminder(self, guild_id: str, delay: float):
        """Schedules and sends a DM reminder after a specified delay."""
        try:
            await asyncio.sleep(delay)
            
            user = await self.bot.fetch_user(self.my_user_id)
            guild = self.bot.get_guild(int(guild_id))
            guild_name = guild.name if guild else "an unknown server"

            if user:
                try:
                    await user.send(
                        f"🔔 Hey! It's been 2 hours since the last bump for **{guild_name}**. "
                        f"Time to bump again! Use `/bump` in the server."
                    )
                    print(f"Sent bump reminder to {user.name} for {guild_name}.")
                except discord.Forbidden:
                    print(f"Could not send DM to {user.name} (DMs blocked).")
                except Exception as e:
                    print(f"Error sending DM for bump reminder: {e}")
            else:
                print(f"Could not find user with ID {self.my_user_id} for bump reminder.")
        except asyncio.CancelledError:
            print(f"Bump reminder for guild {guild_id} was cancelled.")
        finally:
            # Clean up the task from the dictionary once it's done or cancelled
            if guild_id in self.reminder_tasks:
                del self.reminder_tasks[guild_id]

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                invites = await guild.invites()
                self.invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
            except discord.Forbidden:
                print(f"[InviteTracker] Missing permission to read invites in {guild.name}")
        print("[InviteTracker] Invite tracking initialized.")
        
        print("Nab is online!")
        
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            new_invites = await member.guild.invites()
            old_invites = self.invite_cache.get(member.guild.id, {})

            used_invite = None
            for invite in new_invites:
                if invite.code == self.tracked_invite_code and invite.uses > old_invites.get(invite.code, 0):
                    used_invite = invite
                    break

            # Update cache
            self.invite_cache[member.guild.id] = {invite.code: invite.uses for invite in new_invites}

            if used_invite:
                role = discord.utils.get(member.guild.roles, name=self.role_name)
                if role:
                    await member.add_roles(role, reason="Joined using tracked invite")
                    print(f"[InviteTracker] Gave '{self.role_name}' to {member} for invite {used_invite.code}")
                else:
                    print(f"[InviteTracker] Role '{self.role_name}' not found in {member.guild.name}")

        except discord.Forbidden:
            print("[InviteTracker] Missing permissions to manage roles or view invites.")
        except Exception as e:
            print(f"[InviteTracker] Error handling join: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages from the bot itself or system messages
        if message.author.bot or message.author.id == self.bot.user.id:
            return
        
        # --- Disboard Bump Detection ---
        if message.author.id == self.disboard_bot_id and message.guild:
            # Check for common Disboard bump success messages
            content_lower = message.content.lower()
            if "bump done" in content_lower or "successfully bumped" in content_lower:
                guild_id = str(message.guild.id)
                self.last_bump_times[guild_id] = datetime.now()
                self.save_bump_times()
                print(f"Detected bump for guild {message.guild.name} ({guild_id}).")

                # Cancel any existing reminder for this guild
                if guild_id in self.reminder_tasks:
                    self.reminder_tasks[guild_id].cancel()
                    del self.reminder_tasks[guild_id]

                # Schedule a new reminder
                self.reminder_tasks[guild_id] = asyncio.create_task(
                    self._schedule_bump_reminder(guild_id, self.bump_reminder_delay_seconds)
                )
                return # Stop processing if it's a bump message to avoid conflicts with other message handlers
    
        if self.bot.user in message.mentions:
            await message.channel.send("Go away I'm busy :S")
        elif message.content.lower() == "nii nii":
            async for last_message in message.channel.history(limit=1):
                if last_message.author == self.bot.user and last_message.content == "Nii nii":
                    return
            await message.channel.send("Nee nee")
        elif message.content.lower() == "nee nee":
            async for last_message in message.channel.history(limit=1):
                if last_message.author == self.bot.user and last_message.content == "Nee nee":
                    return
            await message.channel.send("Nii nee")
        elif message.content.lower() == "nii nee":
            async for last_message in message.channel.history(limit=1):
                if last_message.author == self.bot.user and last_message.content == "Nii nee":
                    return
            await message.channel.send("Nii nii")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.emoji.name == '📌':  # Added the pin emoji
            guild = discord.utils.get(self.bot.guilds, id=payload.guild_id)
            member = discord.utils.get(guild.members, id=payload.user_id)
            role_names = [role.name for role in member.roles]
            if "II" in role_names or "I" in role_names:
                channel = self.bot.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                pinned_text = message.content
                pinned_by = message.author
                destination_channel = self.bot.get_channel(1389744403366678583)

                # Get or create webhook for the destination channel
                webhooks = await destination_channel.webhooks()
                webhook = None
                for wh in webhooks:
                    if wh.name == "Pin Bot Webhook":
                        webhook = wh
                        break
                
                if webhook is None:
                    webhook = await destination_channel.create_webhook(name="Pin Bot Webhook")

                # Send message using webhook to mimic original author
                username = pinned_by.nick or pinned_by.name
                avatar_url = pinned_by.avatar.url if pinned_by.avatar else pinned_by.default_avatar.url
                
                # Send text content if it exists
                if pinned_text:
                    await webhook.send(
                        content=pinned_text,
                        username=username,
                        avatar_url=avatar_url
                    )

                # Handle attachments
                for attachment in message.attachments:
                    file = discord.File(fp=BytesIO(await attachment.read()), filename=attachment.filename)
                    await webhook.send(
                        file=file,
                        username=username,
                        avatar_url=avatar_url
                    )
        
    @commands.command(name="rape")
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def rape(self, ctx, target: discord.Member = None):
        if target is None:
            await ctx.send("You need to mention someone to rape!", delete_after=30)
            return
        if target == ctx.author:
            await ctx.send("You can't rape yourself... or can you? 廊", delete_after=30)
            return

        msg = random.choice(self.rape_messages).format(
            author=ctx.author.mention,
            target=target.mention
        )
        gif_url = await self.get_random_gif("rape")

        embed = discord.Embed(description=msg)
        embed.set_image(url=gif_url)

        await ctx.send(embed=embed, delete_after=120)
        
        # Schedule removal from used_gifs after cooldown
        await asyncio.sleep(self.gif_cooldown)
        if gif_url in self.used_gifs:
            self.used_gifs.remove(gif_url)


    @commands.command(name="gay")
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def gay(self, ctx, target: discord.Member = None):
        if target is None:
            await ctx.send("You need to mention someone !", delete_after=30)
            return
        if target == ctx.author:
            await ctx.send("You are gay...", delete_after=30)
            return

        msg = f"{target.mention} is gay."
        gif_url = await self.get_random_gif("gay")

        embed = discord.Embed(description=msg)
        embed.set_image(url=gif_url)

        await ctx.send(embed=embed, delete_after=120)
        
        # Schedule removal from used_gifs after cooldown
        await asyncio.sleep(self.gif_cooldown)
        if gif_url in self.used_gifs:
            self.used_gifs.remove(gif_url)
            
    @commands.command(name="lesbian")
    @commands.cooldown(rate=1, per=600, type=commands.BucketType.user)
    async def lesbian(self, ctx, target: discord.Member = None):
        if target is None:
            await ctx.send("You need to mention someone !", delete_after=30)
            return
        if target == ctx.author:
            await ctx.send("You are lesbian...", delete_after=30)
            return

        msg = f"{target.mention} is lesbian."
        gif_url = await self.get_random_gif("lesbian")

        embed = discord.Embed(description=msg)
        embed.set_image(url=gif_url)

        await ctx.send(embed=embed, delete_after=120)
        
        # Schedule removal from used_gifs after cooldown
        await asyncio.sleep(self.gif_cooldown)
        if gif_url in self.used_gifs:
            self.used_gifs.remove(gif_url)
    
    @rape.error
    async def rape_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            minutes = int(error.retry_after // 60)
            seconds = int(error.retry_after % 60)
            await ctx.send(f"⏳ You need to wait {minutes}m {seconds}s before using `rape` again.", delete_after=10)
    @gay.error
    async def gay_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            minutes = int(error.retry_after // 60)
            seconds = int(error.retry_after % 60)
            await ctx.send(f"⏳ You need to wait {minutes}m {seconds}s before using `rape` again.", delete_after=10)
            
    @rape.error
    async def lesbian_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            minutes = int(error.retry_after // 60)
            seconds = int(error.retry_after % 60)
            await ctx.send(f"⏳ You need to wait {minutes}m {seconds}s before using `rape` again.", delete_after=10)
            
    @commands.command(name="testgifs")
    @commands.has_permissions(administrator=True)
    async def testgifs(self, ctx):
        """Posts all rape_gifs to the channel for testing embedding."""
        gif_url = await self.get_random_gif()
        embed = discord.Embed(title="GIF Test")
        embed.set_image(url=gif_url)
        await ctx.send(embed=embed)
        
    @commands.command()
    @commands.cooldown(rate=1, per=10, type=commands.BucketType.user)  # 1 use every 10 seconds per user
    async def whois(self, ctx, user: discord.Member):
        """Gives information about the user mentioned."""
        embed = discord.Embed(color=discord.Color.blue(),
                              title=f"Information about {user.name}")
        embed.set_thumbnail(url=user.avatar)
        embed.add_field(name="Name", value=user.name, inline=True)
        embed.add_field(name="ID", value=user.id, inline=True)
        embed.add_field(name="Nickname", value=user.nick, inline=True)
        embed.add_field(name="Status", value=user.status, inline=True)
        embed.add_field(name="Playing", value=user.activity, inline=True)
        embed.add_field(name="Highest Role", value=user.top_role, inline=True)
        embed.add_field(name="Joined Discord", value=user.created_at.strftime("%b %d %Y %H:%M"), inline=True)
        embed.add_field(name="Joined Server", value=user.joined_at.strftime("%b %d %Y %H:%M"), inline=True)
        os.system("echo \'whois logs: "+ctx.message.content+"\'")
        await ctx.send(embed=embed)
        
    @commands.command(name='say')
    @commands.cooldown(rate=1, per=10, type=commands.BucketType.user)
    async def say(self, ctx, *, message: str):
        # Restrict to specific roles or admins
        if not (
            any(role.name in ALLOWED_ROLES for role in ctx.author.roles)
            or ctx.author.guild_permissions.administrator
        ):
            print(f"[DENIED] {ctx.author} tried to use 'say' without permission.")
            return

        # Deny mentions
        if any(x in message for x in ["@everyone", "@here"]) or ctx.message.mentions or ctx.message.role_mentions:
            print(f"[DENIED] {ctx.author} tried to mention in 'say'.")
            return

        # Deny links
        if re.search(r'https?://|discord\.gg', message):
            print(f"[DENIED] {ctx.author} tried to post a link in 'say'.")
            return

        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print(f"[WARNING] Could not delete message from {ctx.author}.")

        await ctx.send(message)

    @say.error
    async def say_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            print(f"[COOLDOWN] {ctx.author} used 'say' too soon ({round(error.retry_after, 1)}s left).")
        else:
            raise error
    
async def setup(bot):
    await bot.add_cog(Cog1(bot))
