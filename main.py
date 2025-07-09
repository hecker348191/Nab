import os
import discord
from dotenv import load_dotenv
from discord.ext import commands
load_dotenv()
bot = commands.Bot(command_prefix='~', intents=discord.Intents.all(), help_command=None)
from cog1 import Cog1
from reputationcog import ReputationCog
from LQCog import LQCog
from wordcog import WordCounter
from numberscog import NumberCog
from boostcog import BoostCog
from prohibitedwords import ProhibitedWordsCog
from imgpermcog import ImgPermCog
from autodelete import AutoDelete
from roletrackercog import RoleTracker
from roletoggler import RoleToggler
from nineball import NineBall
from wordreactions import WordReactions
from captchacog import CaptchaCog
from inactivitycog import InactivityCog


# Error handler for slash commands (ephemeral messages)
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    embed = discord.Embed(color=discord.Color.red())
    
    if isinstance(error, discord.app_commands.MissingRequiredArgument):
        embed.title = "Missing Required Argument"
        embed.description = f"Missing required argument: `{error.param.name}`"
    elif isinstance(error, discord.app_commands.CommandNotFound):
        embed.title = "Command Not Found"
        embed.description = "This command doesn't exist."
    elif isinstance(error, discord.app_commands.MissingPermissions):
        embed.title = "Missing Permissions"
        embed.description = "You don't have permission to use this command."
    elif isinstance(error, discord.app_commands.BotMissingPermissions):
        embed.title = "Bot Missing Permissions"
        embed.description = "I don't have the required permissions to execute this command."
    elif isinstance(error, discord.app_commands.CommandOnCooldown):
        embed.title = "Command on Cooldown"
        embed.description = f"Command is on cooldown. Try again in {error.retry_after:.2f} seconds."
    else:
        print(f"Unexpected slash command error: {error}")
        embed.title = "Unexpected Error"
        embed.description = "An unexpected error occurred. Please try again later.\n"+str(error)
    
    # Send ephemeral message (only visible to the user)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')

    await bot.add_cog(Cog1(bot))
    await bot.add_cog(LQCog(bot))
    await bot.add_cog(NumberCog(bot))
    await bot.add_cog(WordCounter(bot))
    await bot.add_cog(BoostCog(bot))
    await bot.add_cog(NineBall(bot))
    await bot.add_cog(ProhibitedWordsCog(bot))
    await bot.add_cog(ImgPermCog(bot))
    await bot.add_cog(AutoDelete(bot))
    await bot.add_cog(RoleTracker(bot))
    await bot.add_cog(RoleToggler(bot))
    await bot.add_cog(CaptchaCog(bot))
    await bot.add_cog(InactivityCog(bot))

    # Initialize and assign the ReputationCog properly
    rep_cog = ReputationCog(bot)
    bot.reputation_cog = rep_cog  # ✅ Make it accessible from other cogs
    await bot.add_cog(rep_cog)

    await bot.add_cog(WordReactions(bot))

    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands globally")
    except Exception as e:
        print(f"Failed to sync slash commands globally: {e}")

    # Sync commands instantly to your test guild
    test_guild_id = 1385991417393844224
    if test_guild_id:
        try:
            guild = discord.Object(id=test_guild_id)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to guild {test_guild_id} (instant)")
        except Exception as e:
            print(f"Failed to sync to guild: {e}")
    else:
        print("Set your test guild ID in on_ready() for instant slash command testing")

    # Debug: Show what commands are registered
    print("Registered slash commands:")
    for command in bot.tree.get_commands():
        print(f"  - /{command.name}: {command.description}")


@bot.event
async def on_command_error(ctx, error):
    # Create an ephemeral embed for the error message
    embed = discord.Embed(color=discord.Color.red())
    
    if isinstance(error, commands.MissingRequiredArgument):
        embed.title = "Missing Required Argument"
        embed.description = f"Missing required argument: `{error.param.name}`"
        embed.add_field(name="Usage", value=f"`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`", inline=False)
    elif isinstance(error, commands.BadArgument):
        embed.title = "Invalid Argument"
        embed.description = "Invalid argument provided. Please check your input."
    elif isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands silently
    elif isinstance(error, commands.MissingPermissions):
        embed.title = "Missing Permissions"
        embed.description = "You don't have permission to use this command."
    elif isinstance(error, commands.BotMissingPermissions):
        embed.title = "Bot Missing Permissions"
        embed.description = "I don't have the required permissions to execute this command."
    elif isinstance(error, commands.CommandOnCooldown):
        embed.title = "Command on Cooldown"
        embed.description = f"Command is on cooldown. Try again in {error.retry_after:.2f} seconds."
    elif isinstance(error, commands.NotOwner):
        embed.title = "Owner Only"
        embed.description = "This command can only be used by the bot owner."
    elif isinstance(error, commands.NoPrivateMessage):
        embed.title = "Server Only"
        embed.description = "This command cannot be used in private messages."
    else:
        # Log unexpected errors
        print(f"Unexpected error in command '{ctx.command}': {error}")
        embed.title = "Unexpected Error"
        embed.description = "An unexpected error occurred. Please try again later."
    
    #TODO: Send as ephemeral message (only visible to the user)
    msg = await ctx.send(embed=embed)
    await msg.delete(delay=10)

bot_token = os.getenv("BOT_TOKEN")
if __name__ == '__main__':
    bot.run(bot_token)
