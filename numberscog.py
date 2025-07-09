import json
import discord
from discord.ext import commands

class NumberCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sequential_counter = 1023  # Track the sequential counter separately

    def load_sequential_counter(self):
        """Load the sequential counter from file"""
        try:
            with open('sequential_counter.json', 'r') as f:
                data = json.load(f)
                self.sequential_counter = data.get('counter', 1023)
        except (FileNotFoundError, json.JSONDecodeError):
            self.sequential_counter = 1023

    def save_sequential_counter(self):
        """Save the sequential counter to file"""
        with open('sequential_counter.json', 'w') as f:
            json.dump({'counter': self.sequential_counter}, f, indent=4)

    def get_next_sequential_number(self, user_numbers):
        """Get the next number in the sequential sequence (ignoring manually assigned high numbers)"""
        self.load_sequential_counter()
        
        # Keep incrementing until we find an unused number
        while self.sequential_counter in user_numbers.values():
            self.sequential_counter += 1
        
        next_number = self.sequential_counter
        self.sequential_counter += 1
        self.save_sequential_counter()
        
        return next_number

    @commands.Cog.listener()
    async def on_member_join(self, member):
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        if str(member.id) not in user_numbers:
            user_numbers[str(member.id)] = self.get_next_sequential_number(user_numbers)
        
        with open('user_numbers.json', 'w') as f:
            json.dump(user_numbers, f, indent=4)
        
        await member.edit(nick=f'№{user_numbers[str(member.id)]}')
        
    @commands.command(aliases=['refreshn'])
    async def refresh_numbers(self, ctx):
        """Refreshes and reapplies numbers from the JSON to current members' nicknames."""
        role_names = [role.name for role in ctx.author.roles]
        if "II" not in role_names and "I" not in role_names:
            await ctx.send("You do not have the required role to use this command.")
            return

        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}

        updated_numbers = {}
        count = 0
        removed = 0

        for member in ctx.guild.members:
            user_id = str(member.id)
            if user_id in user_numbers:
                try:
                    number = user_numbers[user_id]
                    await member.edit(nick=f'№{number}')
                    updated_numbers[user_id] = number
                    count += 1
                except discord.Forbidden:
                    await ctx.send(f"Could not update nickname for {member.mention} (insufficient permissions).")
            else:
                continue

        # Remove users from JSON who are no longer in the server
        for user_id in list(user_numbers.keys()):
            if not ctx.guild.get_member(int(user_id)):
                removed += 1

        # Save updated file
        with open('user_numbers.json', 'w') as f:
            json.dump(updated_numbers, f, indent=4)

        await ctx.send(f"Refreshed numbers for {count} members. Removed {removed} entries for users no longer in the server.")

        
    @commands.command()
    async def ln(self, ctx, number: int):
        """Locates and mentions the user with the specified number."""
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        # Find the user with the specified number
        user_id = None
        for uid, num in user_numbers.items():
            if num == number:
                user_id = uid
                break
        
        if user_id:
            user = self.bot.get_user(int(user_id))
            if user:
                await ctx.send(f"№{number} belongs to {user.mention}")
            else:
                await ctx.send(f"№{number} is assigned to user ID {user_id}, but I couldn't find that user.")
        else:
            await ctx.send(f"№{number} is not assigned to anyone.")

    @commands.command()
    async def nn(self, ctx):
        """Checks the next number available."""
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        # Sync with current server nicknames first
        for member in ctx.guild.members:
            if member.nick:
                try:
                    number = int(member.nick.split('№')[1])
                    user_numbers[str(member.id)] = number
                except (ValueError, IndexError):
                    pass
        
        with open('user_numbers.json', 'w') as f:
            json.dump(user_numbers, f, indent=4)
        
        # Show what the next sequential number would be (without actually assigning it)
        self.load_sequential_counter()
        temp_counter = self.sequential_counter
        while temp_counter in user_numbers.values():
            temp_counter += 1
        
        await ctx.send(f"The next number that will be generated is: №{temp_counter}")

    @commands.command()
    async def n(self, ctx, member: discord.Member, number: int):
        """Assigns a new number to a user if not already taken."""
        role_names = [role.name for role in ctx.author.roles]
        if "II" not in role_names and "I" not in role_names:
            await ctx.send("You do not have the required role to use this command.")
            return
        
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        if number in user_numbers.values():
            await ctx.send(f"№{number} is already taken")
            return
        
        user_numbers[str(member.id)] = number
        with open('user_numbers.json', 'w') as f:
            json.dump(user_numbers, f, indent=4)
        
        await member.edit(nick=f'№{number}')
        await ctx.send(f"Assigned №{number} to {member.mention}")

    @commands.command()
    async def r(self, ctx, member: discord.Member):
        """Removes a number from a user and assigns them the next sequential number if they're still in the server."""
        role_names = [role.name for role in ctx.author.roles]
        if "II" not in role_names and "I" not in role_names:
            await ctx.send("You do not have the required role to use this command.")
            return
        
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        # Check if the user has a number assigned
        if str(member.id) not in user_numbers:
            await ctx.send(f"{member.mention} doesn't have a number assigned.")
            return
        
        old_number = user_numbers[str(member.id)]
        
        # Remove the user's current number
        del user_numbers[str(member.id)]
        
        # Check if the member is still in the server
        if member in ctx.guild.members:
            # Sync with current server nicknames (excluding the member we're reassigning)
            for guild_member in ctx.guild.members:
                if guild_member.nick and guild_member != member:
                    try:
                        number = int(guild_member.nick.split('№')[1])
                        if str(guild_member.id) != str(member.id):
                            user_numbers[str(guild_member.id)] = number
                    except (ValueError, IndexError):
                        pass
            
            # Get the next sequential number
            next_number = self.get_next_sequential_number(user_numbers)
            
            # Assign the new number
            user_numbers[str(member.id)] = next_number
            
            # Save the updated numbers
            with open('user_numbers.json', 'w') as f:
                json.dump(user_numbers, f, indent=4)
            
            # Update the member's nickname
            try:
                await member.edit(nick=f'№{next_number}')
                await ctx.send(f"Removed №{old_number} from {member.mention} and assigned them №{next_number}")
            except discord.Forbidden:
                await ctx.send(f"Removed №{old_number} from {member.mention} and assigned them №{next_number}, but couldn't update their nickname (insufficient permissions)")
        else:
            # Member is not in the server, just remove their number
            with open('user_numbers.json', 'w') as f:
                json.dump(user_numbers, f, indent=4)
            await ctx.send(f"Removed №{old_number} from {member.mention} (user not in server)")

    @commands.command()
    async def d(self, ctx):
        """Checks for duplicate numbers and prints all duplicates in the chat."""
        with open('user_numbers.json', 'r') as f:
            try:
                user_numbers = json.load(f)
            except json.JSONDecodeError:
                user_numbers = {}
        
        number_counts = {}
        duplicates = []
        
        # Count the occurrences of each number
        for number in user_numbers.values():
            number_counts[number] = number_counts.get(number, 0) + 1
        
        # Find duplicate numbers
        for number, count in number_counts.items():
            if count > 1:
                duplicates.append(number)
        
        if duplicates:
            await ctx.send("Duplicate numbers found:")
            for number in duplicates:
                users = [member for member, num in user_numbers.items() if num == number]
                user_list = ", ".join([f"{self.bot.get_user(int(user))} (№{number})" for user in users])
                await ctx.send(user_list)
        else:
            await ctx.send("No duplicate numbers found.")

    @commands.command()
    async def reset_counter(self, ctx, new_counter: int):
        """Reset the sequential counter to a specific number (admin only)."""
        role_names = [role.name for role in ctx.author.roles]
        if "II" not in role_names and "I" not in role_names:
            await ctx.send("You do not have the required role to use this command.")
            return
        
        self.sequential_counter = new_counter
        self.save_sequential_counter()
        await ctx.send(f"Sequential counter reset to {new_counter}")

def setup(bot):
    bot.add_cog(NumberCog(bot))
