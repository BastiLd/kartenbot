import logging

import discord


async def send_interaction_response(interaction: discord.Interaction, **kwargs):
    try:
        is_done = getattr(interaction.response, "is_done", None)
        if callable(is_done) and is_done():
            return await interaction.followup.send(**kwargs)
        return await interaction.response.send_message(**kwargs)
    except discord.InteractionResponded:
        return await interaction.followup.send(**kwargs)
    except discord.NotFound:
        logging.warning("Interaction expired before response could be sent.")
        return None
    except discord.HTTPException as exc:
        # 10003 = Unknown Channel: Thread/Kanal wurde gelöscht – nur leise loggen.
        if getattr(exc, "code", None) == 10003:
            logging.info("Interaction target channel no longer exists; skipping response.")
            return None
        logging.exception("Failed to send interaction response")
        return None


async def defer_interaction(interaction: discord.Interaction, *, ephemeral: bool | None = None) -> bool:
    is_done = getattr(interaction.response, "is_done", None)
    if callable(is_done) and is_done():
        return True
    defer = getattr(interaction.response, "defer", None)
    if not callable(defer):
        return False
    try:
        if ephemeral is None:
            await defer()
        else:
            await defer(ephemeral=ephemeral)
        return True
    except TypeError:
        try:
            await defer()
            return True
        except discord.InteractionResponded:
            return True
        except discord.NotFound:
            logging.warning("Interaction expired before defer could be sent.")
            return False
        except discord.HTTPException:
            logging.exception("Failed to defer interaction")
            return False
    except discord.InteractionResponded:
        return True
    except discord.NotFound:
        logging.warning("Interaction expired before defer could be sent.")
        return False
    except discord.HTTPException:
        logging.exception("Failed to defer interaction")
        return False


async def edit_interaction_message(interaction: discord.Interaction, **kwargs):
    try:
        return await interaction.response.edit_message(**kwargs)
    except discord.InteractionResponded:
        message = getattr(interaction, "message", None)
        if message is None:
            logging.warning("Interaction already responded and no message is available for followup edit.")
            return None
        try:
            return await interaction.followup.edit_message(message.id, **kwargs)
        except discord.NotFound:
            logging.warning("Interaction message no longer exists for followup edit.")
            return None
        except discord.HTTPException:
            logging.exception("Failed to edit interaction message through followup")
            return None
    except discord.NotFound:
        logging.warning("Interaction message no longer exists for edit.")
        return None
    except discord.HTTPException:
        logging.exception("Failed to edit interaction message")
        return None
