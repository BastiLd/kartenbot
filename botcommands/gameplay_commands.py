from __future__ import annotations

import logging
import random
from types import ModuleType

import discord


def register_gameplay_commands(bot, module: ModuleType) -> dict[str, object]:
    @bot.tree.command(
        name="mission",
        description="Schicke dein Team auf eine Mission und erhalte eine Belohnung",
    )
    async def mission(interaction: discord.Interaction):
        if module.ALPHA_PHASE_ENABLED:
            await module._send_alpha_feature_blocked(interaction)
            return
        if not await module.is_channel_allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        is_admin_user = await module.is_admin(interaction)

        mission_count = 0
        if not is_admin_user:
            mission_count = await module.get_mission_count(interaction.user.id)
            if mission_count >= 2:
                await module._send_ephemeral(
                    interaction,
                    content="? Du hast heute bereits deine 2 Missionen aufgebraucht! Komme morgen wieder.",
                )
                return

        waves = random.randint(2, 6)
        reward_card = module.random_gameplay_card(module.karten, alpha_enabled=module.ALPHA_PHASE_ENABLED)
        mission_title = f"Mission {mission_count + 1}/2" if not is_admin_user else "Mission (Admin)"
        mission_description = "Hier kommt sp?ter die Story. Hier kommt sp?ter die Story."

        mission_data = {
            "waves": waves,
            "reward_card": reward_card,
            "current_wave": 0,
            "player_card": None,
            "title": mission_title,
            "description": mission_description,
        }
        embed = module._build_mission_embed(mission_data)
        mission_thread = await module._create_required_private_mission_thread(interaction)
        if mission_thread is None:
            return

        request_id = await module.create_mission_request(
            guild_id=interaction.guild_id or 0,
            channel_id=mission_thread.id,
            user_id=interaction.user.id,
            mission_data=mission_data,
            visibility=module.VISIBILITY_PRIVATE,
            is_admin=is_admin_user,
        )
        mission_view = module.MissionAcceptView(
            interaction.user.id,
            mission_data,
            request_id=request_id,
            visibility=module.VISIBILITY_PRIVATE,
            is_admin=is_admin_user,
        )
        message = await module._safe_send_channel(interaction, mission_thread, embed=embed, view=mission_view)
        if isinstance(message, discord.Message):
            await module.update_mission_request_message(request_id, message.id, message.channel.id)
            await interaction.followup.send(f"Mission-Thread erstellt: {mission_thread.mention}", ephemeral=True)
        else:
            await interaction.followup.send("? Missions-Anfrage konnte nicht gesendet werden.", ephemeral=True)

    @bot.tree.command(name="geschichte", description="Starte eine interaktive Story")
    async def story(interaction: discord.Interaction):
        if module.ALPHA_PHASE_ENABLED:
            await module._send_alpha_feature_blocked(interaction)
            return
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        visibility = (
            await module.get_message_visibility(interaction.guild_id, visibility_key)
            if visibility_key
            else module.VISIBILITY_PRIVATE
        )
        ephemeral = visibility != module.VISIBILITY_PUBLIC
        view = module.StorySelectView(interaction.user.id)
        embed = discord.Embed(
            title="?? Story ausw?hlen",
            description="W?hle eine Story aus der Liste. Aktuell verf?gbar: **text**",
        )
        await module._send_with_visibility(interaction, visibility_key, embed=embed, view=view)
        await view.wait()
        if not view.value:
            await interaction.followup.send("? Keine Story gew?hlt. Abgebrochen.", ephemeral=ephemeral)
            return

        story_view = module.StoryPlayerView(interaction.user.id, view.value)
        start_embed = story_view.render_step_embed()
        await interaction.followup.send(embed=start_embed, view=story_view, ephemeral=ephemeral)

    @bot.tree.command(name="kampf", description="K?mpfe gegen einen anderen Spieler im 1v1!")
    async def fight(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            logging.exception("Unexpected error")

        public_result_channel_id = interaction.channel_id or 0
        if isinstance(interaction.channel, discord.Thread):
            public_result_channel_id = int(interaction.channel.parent_id or interaction.channel_id or 0)

        user_karten = await module.get_user_karten(interaction.user.id)
        if not user_karten:
            await interaction.followup.send("Du brauchst mindestens 1 Karte f?r den Kampf!", ephemeral=True)
            return

        card_select_view = module.CardSelectView(interaction.user.id, user_karten, 1)
        await interaction.followup.send(
            "W?hle deine Karte f?r den 1v1-Kampf:",
            view=card_select_view,
            ephemeral=True,
        )
        await card_select_view.wait()
        if not card_select_view.value:
            await interaction.followup.send("? Keine Karte gew?hlt. Kampf abgebrochen.", ephemeral=True)
            return

        selected_names = card_select_view.value
        selected_cards = [await module.get_karte_by_name(name) for name in selected_names]

        view = module.OpponentSelectView(interaction.user, interaction.guild)
        await interaction.followup.send("W?hle einen Gegner (User oder Bot):", view=view, ephemeral=True)
        await view.wait()
        if not view.value:
            await interaction.followup.send("? Kein Gegner gew?hlt. Kampf abgebrochen.", ephemeral=True)
            return

        opponent_id = view.value
        if opponent_id == "bot":
            fight_thread = await module._create_required_private_fight_thread(interaction)
            if fight_thread is None:
                return
            target_channel = fight_thread
            bot_card = module.random_gameplay_card(module.karten, alpha_enabled=module.ALPHA_PHASE_ENABLED)
            battle_view = module.BattleView(
                selected_cards[0],
                bot_card,
                interaction.user.id,
                0,
                None,
                public_result_channel_id=public_result_channel_id,
            )
            await battle_view.init_with_buffs()

            class BotUser:
                def __init__(self):
                    self.id = 0
                    self.display_name = "Bot"
                    self.mention = "**Bot**"

            bot_user = BotUser()
            log_message = await module._safe_send_channel(
                interaction,
                target_channel,
                embed=module.create_battle_log_embed(),
            )
            if isinstance(log_message, discord.Message):
                battle_view.battle_log_message = log_message

            embed = module.create_battle_embed(
                selected_cards[0],
                bot_card,
                battle_view.player1_hp,
                battle_view.player2_hp,
                interaction.user.id,
                interaction.user,
                bot_user,
                current_attack_infos=module._build_attack_info_lines(selected_cards[0]),
                recent_log_lines=battle_view._recent_log_lines,
                highlight_tone=battle_view._last_highlight_tone,
            )
            battle_message = await module._safe_send_channel(interaction, target_channel, embed=embed, view=battle_view)
            if battle_message is None:
                return
            if isinstance(battle_message, discord.Message):
                await battle_view.persist_session(target_channel, status="active", battle_message=battle_message)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("\u274c Dieser Command funktioniert nur auf einem Server.", ephemeral=True)
            return
        challenged = guild.get_member(int(opponent_id))
        if not challenged:
            await interaction.followup.send("? Gegner nicht gefunden!", ephemeral=True)
            return
        fight_thread = await module._create_required_private_fight_thread(interaction, challenged=challenged)
        if fight_thread is None:
            return
        target_channel = fight_thread
        thread_created = True

        request_id = await module.create_fight_request(
            guild_id=interaction.guild_id or 0,
            origin_channel_id=public_result_channel_id or 0,
            message_channel_id=getattr(target_channel, "id", 0) or 0,
            thread_id=fight_thread.id,
            thread_created=thread_created,
            challenger_id=interaction.user.id,
            challenged_id=challenged.id,
            challenger_card=selected_cards[0]["name"],
        )
        challenge_view = module.ChallengeResponseView(
            interaction.user.id,
            challenged.id,
            selected_cards[0]["name"],
            request_id=request_id,
            origin_channel_id=public_result_channel_id,
            thread_id=fight_thread.id,
            thread_created=thread_created,
        )
        message = await module._safe_send_channel(
            interaction,
            target_channel,
            content=module._fight_challenge_prompt(challenged.mention, selected_cards[0]["name"]),
            view=challenge_view,
        )
        if message is None:
            await module.claim_fight_request(request_id, "failed")
            await module._maybe_delete_fight_thread(fight_thread.id if fight_thread else None, thread_created)
            return
        await module.update_fight_request_message(request_id, message.id, getattr(message.channel, "id", None))
        await interaction.followup.send(f"Warte auf Antwort von {challenged.mention}...", ephemeral=True)

    return {
        "mission": mission,
        "story": story,
        "fight": fight,
    }
