import discord
import asyncio
import json
from discord.ext import commands, tasks
from collections import defaultdict
from typing import Optional
from datetime import datetime, timezone, timedelta

def has_required_role():
    async def predicate(ctx):
        required_roles = ["I", "II", "III"]
        return any(discord.utils.get(ctx.author.roles, name=role) is not None for role in required_roles)
    return commands.check(predicate)

class AutoDelete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watchdog.start()
        self.config = {}
        self.deletion_queues = defaultdict(list)  # channel_id: list[discord.Message]
        self.queue_task = self.process_queues.start()
        self.channel_cooldowns = {}  # channel_id: cooldown_end_time
        self.cooldown_duration = 600  # Cooldown duration in seconds (10 minutes)
        self.sleep_duration = 1.1  # Initial sleep duration
        self.min_sleep_reached = False  # Flag to track if minimum sleep duration is reached

        try:
            with open("autodelete.json", "r") as f:
                self.config = json.load(f)
        except FileNotFoundError:
            self.config = {}

    def cog_unload(self):
        self.queue_task.cancel()

    async def save_config(self):
        with open("autodelete.json", "w") as f:
            json.dump(self.config, f)
            
    @commands.Cog.listener()
    async def on_ready(self):
        if not self.process_queues.is_running():
            self.process_queues.start()
            print("[AutoDelete] Restarted process_queues loop on ready")


    @commands.command()
    @has_required_role()
    async def autodelete(self, ctx, limit: int, time: int, unit: str):
        """Sets an autodelete limit and timer for chat, e.g., ~autodelete 50 5 hours"""
        channel = ctx.channel
        if unit == "minutes":
            time *= 60
        elif unit == "hours":
            time *= 3600

        self.config[str(channel.id)] = {"limit": limit, "time": time}
        await self.save_config()
        await ctx.send(f"️ Auto delete set to delete messages after {limit} messages or {time} seconds in {channel.mention}")

    @commands.command()
    @has_required_role()
    async def clear(self, ctx, amount: Optional[int] = None):
        """Clears the given amount of messages in chat, e.g., ~clear 20"""
        if amount is None:
            async for message in ctx.channel.history(limit=None):
                if message.id == ctx.message.id:
                    break
                await message.delete()
            return

        amount = min(amount, 100)
        messages = []
        async for message in ctx.channel.history(limit=amount):
            messages.append(message)

        await ctx.channel.delete_messages(messages)

    @commands.command()
    @has_required_role()
    async def clearold(self, ctx, amount: Optional[int] = None):
        """Clears the given amount of **oldest** messages in chat, e.g., ~clearold 20"""
        if amount is None:
            messages = []
            async for message in ctx.channel.history(limit=None, oldest_first=True):
                if message.id == ctx.message.id or message.pinned:
                    continue
                messages.append(message)
            
            # Delete in chunks of 100 (Discord API limit)
            for i in range(0, len(messages), 100):
                chunk = messages[i:i + 100]
                try:
                    await ctx.channel.delete_messages(chunk)
                except discord.errors.ClientException as e:
                    print(f"[clearold] ClientException: {e}")
                    await ctx.send(f"Could not delete messages: {e}", delete_after=5)
                    return
                except discord.errors.HTTPException as e:
                    print(f"[clearold] HTTPException: {e}")
                    # Handle individual deletion if bulk deletion fails
                    for msg in chunk:
                        try:
                            await msg.delete()
                            await asyncio.sleep(1.1)  # Add delay for individual deletion
                        except Exception as e:
                            print(f"[clearold] Individual delete failed: {e}")
                await asyncio.sleep(1.1)  # Add delay between chunks

        else:
            amount = min(amount, 100)
            messages = []
            async for message in ctx.channel.history(limit=1000, oldest_first=True):
                if message.id == ctx.message.id or message.pinned:
                    continue
                messages.append(message)
                if len(messages) >= amount:
                    break

            try:
                await ctx.channel.delete_messages(messages)
            except discord.errors.ClientException as e:
                print(f"[clearold] ClientException: {e}")
                await ctx.send(f"Could not delete messages: {e}", delete_after=5)
                return
            except discord.errors.HTTPException as e:
                print(f"[clearold] HTTPException: {e}")
                # Handle individual deletion if bulk deletion fails
                for msg in messages:
                    try:
                        await msg.delete()
                        await asyncio.sleep(1.1)  # Add delay for individual deletion
                    except Exception as e:
                        print(f"[clearold] Individual delete failed: {e}")

        await ctx.message.delete()

    @commands.Cog.listener()
    @commands.has_permissions(manage_messages=True)
    async def on_message(self, message):
        try:
            if message.author.bot:
                return

            config = self.config.get(str(message.channel.id))
            if not config:
                return

            limit = config["limit"]
            messages = []

            async for msg in message.channel.history(limit=limit):
                if not msg.pinned:
                    messages.append(msg)

            if len(messages) >= limit:
                self.deletion_queues[message.channel.id].append(messages[-1])
        except Exception as e:
            print(f"[AutoDelete] Error in on_message: {e}")

    async def is_channel_on_cooldown(self, channel_id):
        """Checks if a channel is on cooldown."""
        if channel_id in self.channel_cooldowns:
            cooldown_end_time = self.channel_cooldowns[channel_id]
            if datetime.now(timezone.utc) < cooldown_end_time:
                return True
            else:
                del self.channel_cooldowns[channel_id]  # Remove expired cooldown
        return False

    async def apply_channel_cooldown(self, channel_id):
        """Applies a cooldown to a channel."""
        self.channel_cooldowns[channel_id] = datetime.now(timezone.utc) + timedelta(seconds=self.cooldown_duration)
        print(f"[AutoDelete] Applying cooldown to channel {channel_id} for {self.cooldown_duration} seconds")

    async def adjust_sleep_duration(self, rate_limited: bool):
        """Adjusts the sleep duration based on rate limits."""
        if rate_limited:
            self.sleep_duration = min(self.sleep_duration * 2, 10)  # Increase sleep, max 10s
            print(f"[AutoDelete] Rate limit detected, increasing sleep to {self.sleep_duration}")
            self.min_sleep_reached = False  # Reset the flag when rate limit is detected
        else:
            new_sleep_duration = max(1.1, self.sleep_duration * 0.75)  # Decrease sleep, min 1.1s
            if new_sleep_duration == 1.1:
                self.sleep_duration = new_sleep_duration
                if not self.min_sleep_reached:
                    print(f"[AutoDelete] No rate limit, decreasing sleep to {self.sleep_duration}")
                    self.min_sleep_reached = True
            else:
                self.sleep_duration = new_sleep_duration
                if not self.min_sleep_reached:
                    print(f"[AutoDelete] No rate limit, decreasing sleep to {self.sleep_duration}")
           
    @commands.command()
    @has_required_role()
    async def autodeletestatus(self, ctx):
        """Check if the autodelete loop is running"""
        running = self.process_queues.is_running()
        await ctx.send(f" AutoDelete loop running: `{running}`")
        
    @tasks.loop(minutes=5)
    async def watchdog(self):
        if not self.process_queues.is_running():
            try:
                self.process_queues.start()
                print("[AutoDelete] Watchdog restarted process_queues loop")
            except RuntimeError:
                # Already running
                pass

    @tasks.loop(seconds=10)
    async def process_queues(self):
        try:
            for channel_id, queue in list(self.deletion_queues.items()):
                if await self.is_channel_on_cooldown(channel_id):
                    print(f"[AutoDelete] Skipping channel {channel_id} due to cooldown")
                    continue

                if not queue:
                    continue

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    continue

                messages_to_delete = []
                while queue and len(messages_to_delete) < 100:
                    msg = queue.pop(0)
                    if (datetime.now(timezone.utc) - msg.created_at) < timedelta(days=14):
                        messages_to_delete.append(msg)

                if messages_to_delete:
                    try:
                        await channel.delete_messages(messages_to_delete)
                        await self.adjust_sleep_duration(rate_limited=False)  # No rate limit
                    except discord.errors.HTTPException as e:
                        print(f"[AutoDelete] Bulk delete failed: {e}")
                        await self.apply_channel_cooldown(channel_id)  # Apply cooldown
                        await self.adjust_sleep_duration(rate_limited=True)  # Rate limit detected
                        # Fallback to individual deletion with increased delay
                        for msg in messages_to_delete:
                            try:
                                await msg.delete()
                                await asyncio.sleep(self.sleep_duration)  # Respect dynamic sleep
                            except Exception as e:
                                print(f"[AutoDelete] Failed individual delete: {e}")
                    await asyncio.sleep(self.sleep_duration)  # Use dynamic sleep duration
        except Exception as e:
            print(f"[AutoDelete] Error in process_queues loop: {e}")


def setup(bot):
    bot.add_cog(AutoDelete(bot))
