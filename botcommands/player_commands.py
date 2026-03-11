from __future__ import annotations

import logging
import random
from types import ModuleType

import discord
from discord import app_commands


def register_player_commands(bot, module: ModuleType) -> dict[str, object]:
    @bot.tree.command(name="täglich", description="Hole deine tägliche Belohnung ab")
    async def täglich(interaction: discord.Interaction):
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        now = int(module.time.time())
        is_admin_user = await module.is_admin(interaction)
        async with module.db_context() as db:
            cursor = await db.execute(
                "SELECT last_daily FROM user_daily WHERE user_id = ?",
                (interaction.user.id,),
            )
            row = await cursor.fetchone()
            if (not is_admin_user) and row and row[0] and now - row[0] < 86400:
                stunden = int((86400 - (now - row[0])) / 3600)
                await module._send_ephemeral(
                    interaction,
                    content=f"Du kannst deine tägliche Belohnung erst in {stunden} Stunden abholen.",
                )
                return
            await db.execute(
                "INSERT OR REPLACE INTO user_daily (user_id, last_daily) VALUES (?, ?)",
                (interaction.user.id, now),
            )
            await db.commit()

        user_id = interaction.user.id
        karte = random.choice(module.karten)

        is_new_card = await module.check_and_add_karte(user_id, karte)
        card_name_text = str(karte.get("name") or "Unbekannte Karte")
        embed_color = module._card_rarity_color(karte)
        image_url = str(karte.get("bild") or "").strip()
        if not image_url:
            fallback_card = module._card_by_name_local(card_name_text)
            image_url = str((fallback_card or {}).get("bild") or "").strip()

        if is_new_card:
            embed = discord.Embed(
                title="🎁 Tägliche Belohnung",
                description="Du hast eine tägliche Belohnung erhalten:",
                color=embed_color,
            )
            embed.add_field(
                name="Karte",
                value=module._card_name_ansi_block(card_name_text, karte),
                inline=False,
            )
            if image_url:
                embed.set_image(url=image_url)
            await module._send_with_visibility(interaction, visibility_key, embed=embed)
            return

        embed = discord.Embed(
            title="💎 Tägliche Belohnung - Infinitydust!",
            description="Du hattest diese Karte bereits:",
            color=embed_color,
        )
        embed.add_field(
            name="Karte",
            value=module._card_name_ansi_block(card_name_text, karte),
            inline=False,
        )
        embed.add_field(
            name="Umwandlung",
            value="Die Karte wurde zu **Infinitydust** umgewandelt!",
            inline=False,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        if image_url:
            embed.set_image(url=image_url)
        await module._send_with_visibility(interaction, visibility_key, embed=embed)

    @bot.tree.command(
        name="eingeladen",
        description="Wähle wer dich eingeladen hat - beide erhalten 1x Infinitydust [Einmalig]",
    )
    async def eingeladen(interaction: discord.Interaction):
        try:
            logging.info(
                "[INVITED] command invoked user=%s guild=%s channel=%s",
                interaction.user.id,
                interaction.guild_id,
                interaction.channel_id,
            )
            visibility_key = module.command_visibility_key_for_interaction(interaction)
            visibility = (
                await module.get_message_visibility(interaction.guild_id, visibility_key)
                if visibility_key
                else module.VISIBILITY_PRIVATE
            )
            ephemeral = visibility != module.VISIBILITY_PUBLIC
            await interaction.response.defer(ephemeral=ephemeral)

            user_id = interaction.user.id
            is_admin_user = await module.is_admin(interaction)
            logging.info("[INVITED] is_admin_user=%s user=%s", is_admin_user, interaction.user.id)

            async with module.db_context() as db:
                if not is_admin_user:
                    cursor = await db.execute(
                        "SELECT used_invite FROM user_daily WHERE user_id = ?",
                        (user_id,),
                    )
                    row = await cursor.fetchone()
                    if row and row[0] == 1:
                        await interaction.followup.send(
                            "❌ Du hast den `/eingeladen` Command bereits verwendet! Nur Admins können ihn mehrfach nutzen.",
                            ephemeral=True,
                        )
                        return

                cursor = await db.execute("SELECT DISTINCT user_id FROM user_karten")
                user_rows = await cursor.fetchall()

                cursor = await db.execute("SELECT DISTINCT user_id FROM user_daily")
                daily_rows = await cursor.fetchall()

                cursor = await db.execute("SELECT DISTINCT user_id FROM user_infinitydust")
                dust_rows = await cursor.fetchall()

                all_user_ids = set()
                for row in user_rows + daily_rows + dust_rows:
                    all_user_ids.add(row[0])

                all_user_ids.discard(user_id)
                logging.info("[INVITED] candidates_found=%s user=%s", len(all_user_ids), user_id)

                if not all_user_ids:
                    await interaction.followup.send(
                        "❌ Keine anderen Spieler gefunden! Es müssen andere Spieler den Bot bereits genutzt haben.",
                        ephemeral=True,
                    )
                    return

            view = module.InviteUserSelectView(user_id, list(all_user_ids))
            if is_admin_user:
                description = (
                    "Wähle aus, wer dich eingeladen hat!\n\n"
                    "**Beide erhaltet ihr 1x Infinitydust** 💎\n\n"
                    "👑 **Du bist Admin - kannst unendlich oft einladen!**"
                )
            else:
                description = (
                    "Wähle aus, wer dich eingeladen hat!\n\n"
                    "**Beide erhaltet ihr 1x Infinitydust** 💎\n\n"
                    "⚠️ **Dieser Command kann nur einmal verwendet werden!**"
                )

            embed = discord.Embed(
                title="🎁 Wer hat dich eingeladen?",
                description=description,
                color=0x9D4EDD,
            )
            embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)

        except discord.NotFound:
            logging.info("Invite interaction message no longer exists")
        except Exception:
            logging.exception("Fehler in eingeladen command")
            try:
                await interaction.followup.send(
                    "❌ Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
                    ephemeral=True,
                )
            except Exception:
                logging.exception("Unexpected error")

    @bot.tree.command(name="verbessern", description="Verstärke deine Karten mit Infinitydust")
    async def fuse(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        user_id = interaction.user.id
        user_dust = await module.get_infinitydust(user_id)

        if user_dust < module.FUSE_DUST_COST:
            embed = discord.Embed(
                title="❌ Nicht genug Infinitydust",
                description=(
                    f"Du hast nur **{user_dust} Infinitydust**.\n"
                    f"Du brauchst mindestens **{module.FUSE_DUST_COST} Infinitydust** zum Verstärken!"
                ),
                color=0xFF0000,
            )
            await module._send_ephemeral(interaction, embed=embed)
            return

        view = module.DustAmountView(user_dust)
        embed = discord.Embed(
            title="💎 Karten-Verstärkung",
            description=(
                f"Du hast **{user_dust} Infinitydust**\n\n"
                "Wähle die Menge für die Verstärkung:\n\n"
                f"💎 **{module.FUSE_DUST_COST} Dust** = +{module.FUSE_HEALTH_BONUS} Leben "
                f"oder +{module.FUSE_DAMAGE_MAX_BONUS} Max-Schaden"
            ),
            color=0x9D4EDD,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await module._send_with_visibility(interaction, visibility_key, embed=embed, view=view)

    @bot.tree.command(name="sammlung", description="Zeige deine Karten-Sammlung")
    async def vault(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        user_id = interaction.user.id
        user_karten = await module.get_user_karten(user_id)
        infinitydust = await module.get_infinitydust(user_id)

        if not user_karten and infinitydust == 0:
            await module._send_ephemeral(
                interaction,
                content="Du hast noch keine Karten in deiner Sammlung.",
            )
            return

        embed = discord.Embed(
            title="🗄️ Deine Karten-Sammlung",
            description=f"Du besitzt **{len(user_karten)}** verschiedene Karten:",
        )

        if infinitydust > 0:
            embed.add_field(name="💎 Infinitydust", value=f"Anzahl: {infinitydust}x", inline=True)
            embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")

        for kartenname, anzahl in user_karten[:10]:
            karte = await module.get_karte_by_name(kartenname)
            if karte:
                embed.add_field(
                    name=f"{karte['name']} (x{anzahl})",
                    value=karte["beschreibung"][:100] + "...",
                    inline=False,
                )

        if len(user_karten) > 10:
            embed.set_footer(text=f"Und {len(user_karten) - 10} weitere Karten...")

        view = module.VaultView(interaction.user.id, user_karten)
        await module._send_with_visibility(interaction, visibility_key, embed=embed, view=view)

    @bot.tree.command(
        name="anfang",
        description="Zeigt das Startmenü mit Schnellzugriff auf wichtige Funktionen",
    )
    @app_commands.describe(action="Optional: /anfang aktualisieren oder /anfang lastaktu")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="aktualisieren", value="aktualisieren"),
            app_commands.Choice(name="lastaktu", value="lastaktu"),
        ]
    )
    async def anfang(interaction: discord.Interaction, action: str | None = None):
        if not await module.is_channel_allowed(interaction):
            return

        text = module.build_anfang_intro_text()
        view = module.AnfangView()
        if interaction.guild is None:
            await interaction.response.send_message(content=text, view=view)
            return

        visibility_key = module.command_visibility_key_for_interaction(interaction)
        visibility = (
            await module.get_message_visibility(interaction.guild_id, visibility_key)
            if visibility_key
            else module.VISIBILITY_PRIVATE
        )
        is_admin_user = await module.is_admin(interaction)

        if action:
            if not is_admin_user:
                await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
                return
            if action == "lastaktu":
                existing = await module.get_latest_anfang_message(interaction.guild_id)
                if not existing:
                    await interaction.response.send_message(
                        "ℹ️ Es gibt noch keine gespeicherte /anfang-Nachricht.",
                        ephemeral=True,
                    )
                    return
                channel_id, message_id = existing
                link = f"https://discord.com/channels/{interaction.guild_id}/{channel_id}/{message_id}"
                await interaction.response.send_message(
                    f"🔗 Letzte /anfang-Nachricht: {link}",
                    ephemeral=True,
                )
                return
            if action == "aktualisieren":
                existing = await module.get_latest_anfang_message(interaction.guild_id)
                if not existing:
                    await interaction.response.send_message(
                        "ℹ️ Keine gespeicherte /anfang-Nachricht gefunden. Nutze zuerst `/anfang`.",
                        ephemeral=True,
                    )
                    return
                old_channel_id, old_message_id = existing
                try:
                    old_channel = interaction.guild.get_channel(old_channel_id) or await interaction.guild.fetch_channel(
                        old_channel_id
                    )
                    if not isinstance(old_channel, (discord.TextChannel, discord.Thread)):
                        await interaction.response.send_message(
                            "❌ Kanal der gespeicherten Nachricht nicht gefunden.",
                            ephemeral=True,
                        )
                        return
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.edit(content=text, view=view)
                    await module.set_latest_anfang_message(
                        interaction.guild_id,
                        old_channel_id,
                        old_message_id,
                        interaction.user.id,
                    )
                    await interaction.response.send_message("✅ /anfang aktualisiert.", ephemeral=True)
                except Exception:
                    logging.exception("Failed to edit latest /anfang message")
                    await interaction.response.send_message(
                        "❌ Konnte die gespeicherte /anfang-Nachricht nicht aktualisieren.",
                        ephemeral=True,
                    )
                return

        if is_admin_user:
            existing = await module.get_latest_anfang_message(interaction.guild_id)

            if existing:
                old_channel_id, old_message_id = existing
                try:
                    old_channel = interaction.guild.get_channel(old_channel_id) or await interaction.guild.fetch_channel(
                        old_channel_id
                    )
                    if isinstance(old_channel, (discord.TextChannel, discord.Thread)):
                        old_message = await old_channel.fetch_message(old_message_id)
                        await old_message.edit(view=None)
                except Exception:
                    pass

            sent_message = None
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(content=text, view=view)
                    sent_message = await interaction.original_response()
                else:
                    sent_message = await interaction.followup.send(content=text, view=view)
            except Exception:
                logging.exception("Failed to send /anfang message")
                return

            await module.set_latest_anfang_message(
                interaction.guild_id,
                sent_message.channel.id,
                sent_message.id,
                interaction.user.id,
            )
            return

        if visibility == module.VISIBILITY_PUBLIC:
            await module._send_with_visibility(interaction, visibility_key, content=text, view=view)
        else:
            await module._send_ephemeral(interaction, content=text, view=view)

    return {
        "täglich": täglich,
        "eingeladen": eingeladen,
        "fuse": fuse,
        "vault": vault,
        "anfang": anfang,
    }
