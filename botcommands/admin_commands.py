from __future__ import annotations

from types import ModuleType

import discord
from discord import app_commands


def register_admin_commands(bot, module: ModuleType) -> dict[str, object]:
    configure_group = app_commands.Group(name="konfigurieren", description="Nur für Admins!!!")

    @bot.tree.command(name="kanal-freigeben", description="Nur für Admins!!!")
    async def add_channel_shortcut(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        if not interaction.guild_id or not interaction.channel_id:
            await module._send_ephemeral(
                interaction,
                content="❌ Dieser Command funktioniert nur in einem Server-Kanal.",
            )
            return
        async with module.db_context() as db:
            await db.execute(
                "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
                (interaction.guild_id, interaction.channel_id),
            )
            await db.commit()
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content=f"✅ Hinzugefügt: {interaction.channel.mention}",
        )

    @configure_group.command(name="hinzufuegen", description="Nur für Admins!!!")
    async def configure_add(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        async with module.db_context() as db:
            await db.execute(
                "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
                (interaction.guild_id, interaction.channel_id),
            )
            await db.commit()
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content=f"✅ Hinzugefügt: {interaction.channel.mention}",
        )

    @configure_group.command(name="entfernen", description="Nur für Admins!!!")
    async def configure_remove(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        async with module.db_context() as db:
            await db.execute(
                "DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?",
                (interaction.guild_id, interaction.channel_id),
            )
            await db.commit()
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content=f"🗑️ Entfernt: {interaction.channel.mention}",
        )

    @configure_group.command(name="liste", description="Nur für Admins!!!")
    async def configure_list(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        async with module.db_context() as db:
            cursor = await db.execute(
                "SELECT channel_id FROM guild_allowed_channels WHERE guild_id = ?",
                (interaction.guild_id,),
            )
            rows = await cursor.fetchall()
        if not rows:
            await module._send_with_visibility(
                interaction,
                visibility_key,
                content="ℹ️ Es sind noch keine Kanäle erlaubt. Nutze `/konfigurieren hinzufuegen` im gewünschten Kanal.",
            )
            return
        mentions = "\n".join(f"• <#{row[0]}>" for row in rows)
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content=f"✅ Erlaubte Kanäle:\n{mentions}",
        )

    bot.tree.add_command(configure_group)

    @bot.tree.command(name="intro-zuruecksetzen", description="Nur für Admins!!!")
    async def reset_intro(interaction: discord.Interaction):
        if not await module.is_admin(interaction):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module.send_reset_intro(interaction, visibility_key=visibility_key)

    @bot.tree.command(name="sammlung-ansehen", description="Nur für Admins!!!")
    async def vaultlook(interaction: discord.Interaction):
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        visibility = (
            await module.get_message_visibility(interaction.guild_id, visibility_key)
            if visibility_key
            else module.VISIBILITY_PRIVATE
        )
        ephemeral = visibility != module.VISIBILITY_PUBLIC
        await interaction.response.defer(ephemeral=ephemeral)
        if not await module.is_admin(interaction):
            await interaction.followup.send(
                "❌ Du hast keine Berechtigung für diesen Command! Nur Admins können in andere Vaults schauen.",
                ephemeral=True,
            )
            return

        view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
        await interaction.followup.send(
            "Wähle einen User, dessen Vault du ansehen möchtest:",
            view=view,
            ephemeral=ephemeral,
        )
        await view.wait()

        if not view.value:
            await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
            return

        target_user_id = int(view.value)
        target_user = interaction.guild.get_member(target_user_id)
        if not target_user:
            await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
            return

        await module.send_vaultlook(
            interaction,
            target_user_id,
            target_user.display_name,
            visibility_key=visibility_key,
        )

    @bot.tree.command(name="test-bericht", description="Nur für Admins!!!")
    async def test_bericht(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message("❌ Du hast keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module._send_ephemeral(interaction, content="🔍 Sammle verfügbare Commands...")
        await module.send_test_report(interaction, visibility_key=visibility_key)

    @bot.tree.command(name="karte-geben", description="Nur für Admins!!!")
    async def give(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message(
                "❌ Du hast keine Berechtigung für diesen Command! Nur Admins/Owner können Karten geben.",
                ephemeral=True,
            )
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        visibility = (
            await module.get_message_visibility(interaction.guild_id, visibility_key)
            if visibility_key
            else module.VISIBILITY_PRIVATE
        )
        ephemeral = visibility != module.VISIBILITY_PUBLIC

        user_select_view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content="Wähle einen Nutzer, dem du eine Karte geben möchtest:",
            view=user_select_view,
        )
        await user_select_view.wait()

        if not user_select_view.value:
            await interaction.followup.send("⏰ Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
            return

        target_user_id = int(user_select_view.value)
        target_user = interaction.guild.get_member(target_user_id)
        if not target_user:
            await interaction.followup.send("❌ Nutzer nicht gefunden!", ephemeral=True)
            return

        card_select_view = module.GiveCardSelectView(interaction.user.id, target_user_id)
        await interaction.followup.send(
            f"Wähle eine Karte für {target_user.mention}:",
            view=card_select_view,
            ephemeral=ephemeral,
        )
        await card_select_view.wait()

        if not card_select_view.value:
            await interaction.followup.send("⏰ Keine Karte gewählt. Abgebrochen.", ephemeral=ephemeral)
            return

        selected_card_name = card_select_view.value
        if selected_card_name == "infinitydust":
            amount_view = module.InfinitydustAmountView(interaction.user.id, target_user_id)
            await interaction.followup.send(
                f"Wähle die Menge Infinitydust für {target_user.mention}:",
                view=amount_view,
                ephemeral=ephemeral,
            )
            await amount_view.wait()

            if not amount_view.value:
                await interaction.followup.send("⏰ Keine Menge gewählt. Abgebrochen.", ephemeral=ephemeral)
                return

            amount = amount_view.value
            await module.add_infinitydust(target_user_id, amount)

            embed = discord.Embed(
                title="💎 Infinitydust verschenkt!",
                description=f"{interaction.user.mention} hat **{amount}x Infinitydust** an {target_user.mention} gegeben!",
            )
            embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
            await module._send_with_visibility(interaction, visibility_key, embed=embed)
            return

        selected_card = await module.get_karte_by_name(selected_card_name)
        is_new_card = await module.check_and_add_karte(target_user_id, selected_card)
        selected_color = (
            module._card_rarity_color(selected_card)
            if selected_card
            else module._card_rarity_color(module._card_by_name_local(selected_card_name))
        )

        if is_new_card:
            embed = discord.Embed(
                title="🎁 Karte verschenkt!",
                description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!",
                color=selected_color,
            )
            if selected_card:
                embed.set_image(url=selected_card["bild"])
            await module._send_with_visibility(interaction, visibility_key, embed=embed)
            return

        embed = discord.Embed(
            title="💎 Karte verschenkt - Infinitydust!",
            description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!",
            color=selected_color,
        )
        embed.add_field(
            name="Umwandlung",
            value=f"{target_user.mention} hatte die Karte bereits - wurde zu **Infinitydust** umgewandelt!",
            inline=False,
        )
        embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
        await module._send_with_visibility(interaction, visibility_key, embed=embed)

    @bot.tree.command(name="op-verwaltung", description="Nur für Admins!!!")
    @app_commands.guild_only()
    async def give_op(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if interaction.guild is None:
            await module._send_ephemeral(interaction, content="Dieser Command ist nur in Servern verfügbar.")
            return
        if not await module.is_admin(interaction):
            await module._send_ephemeral(
                interaction,
                content="❌ Du hast keine Berechtigung für diesen Command. Nur Admins können `/op-verwaltung` nutzen.",
            )
            return

        action_view = module.GiveOpActionView(interaction.user.id)
        await module._send_ephemeral(
            interaction,
            content=(
                "Wähle eine Aktion:\n"
                "- `card give user:<id> card:<name>`\n"
                "- `card remove user:<id> card:<name>`\n"
                "- `card give-group user:<id> rarity:common`\n"
                "- `card remove-group user:<id> rarity:common`\n"
                "- `ad user`"
            ),
            view=action_view,
        )
        await action_view.wait()
        action = action_view.value
        if not action:
            await interaction.followup.send("⏰ Keine Aktion gewählt. Abgebrochen.", ephemeral=True)
            return

        if action in {"card_give", "card_remove", "group_give", "group_remove", "add_user", "remove_user"}:
            user_select_view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
            await interaction.followup.send("Wähle den Ziel-Nutzer:", view=user_select_view, ephemeral=True)
            await user_select_view.wait()
            if not user_select_view.value:
                await interaction.followup.send("⏰ Keine Nutzer-Auswahl. Abgebrochen.", ephemeral=True)
                return
            target_user_id = int(user_select_view.value)
            target_member = interaction.guild.get_member(target_user_id)
            target_name = target_member.mention if target_member else f"`{target_user_id}`"
        else:
            target_user_id = 0
            target_name = ""

        if action == "card_give":
            card_select_view = module.CardSelectPagerView(interaction.user.id, module.karten)
            await interaction.followup.send("Wähle eine Karte:", view=card_select_view, ephemeral=True)
            await card_select_view.wait()
            if not card_select_view.value:
                await interaction.followup.send("⏰ Keine Karte gewählt. Abgebrochen.", ephemeral=True)
                return
            card_name = card_select_view.value
            await module.add_karte_amount(target_user_id, card_name, 1)
            await interaction.followup.send(f"✅ `{card_name}` wurde {target_name} gegeben.", ephemeral=True)
            return

        if action == "card_remove":
            card_select_view = module.CardSelectPagerView(interaction.user.id, module.karten)
            await interaction.followup.send(
                "Wähle die Karte, die entfernt werden soll:",
                view=card_select_view,
                ephemeral=True,
            )
            await card_select_view.wait()
            if not card_select_view.value:
                await interaction.followup.send("⏰ Keine Karte gewählt. Abgebrochen.", ephemeral=True)
                return
            card_name = card_select_view.value
            removed = await module.remove_karte_amount(target_user_id, card_name, 1)
            if removed <= 0:
                await interaction.followup.send(f"⚠️ {target_name} besitzt `{card_name}` nicht.", ephemeral=True)
                return
            await interaction.followup.send(f"✅ `{card_name}` wurde {target_name} weggenommen.", ephemeral=True)
            return

        if action in {"group_give", "group_remove"}:
            grouped_cards = module._cards_by_rarity_group()
            rarity_keys = sorted(grouped_cards.keys())
            rarity_view = module.GiveOpRaritySelectView(interaction.user.id, rarity_keys)
            await interaction.followup.send(
                "Wähle die Karten-Gruppe (Seltenheit):",
                view=rarity_view,
                ephemeral=True,
            )
            await rarity_view.wait()
            rarity_key = rarity_view.value
            if not rarity_key:
                await interaction.followup.send("⏰ Keine Gruppe gewählt. Abgebrochen.", ephemeral=True)
                return
            cards_for_group = grouped_cards.get(rarity_key, [])
            if not cards_for_group:
                await interaction.followup.send(
                    "❌ Für diese Gruppe wurden keine Karten gefunden.",
                    ephemeral=True,
                )
                return

            changed_count = 0
            if action == "group_give":
                for card in cards_for_group:
                    card_name = str(card.get("name", "")).strip()
                    if not card_name:
                        continue
                    await module.add_karte_amount(target_user_id, card_name, 1)
                    changed_count += 1
                await interaction.followup.send(
                    f"✅ {target_name} hat {changed_count} Karte(n) aus `{module._rarity_label_from_key(rarity_key)}` erhalten.",
                    ephemeral=True,
                )
                return

            for card in cards_for_group:
                card_name = str(card.get("name", "")).strip()
                if not card_name:
                    continue
                removed = await module.remove_karte_amount(target_user_id, card_name, 1)
                if removed > 0:
                    changed_count += 1
            await interaction.followup.send(
                f"✅ {target_name}: {changed_count} Karte(n) aus `{module._rarity_label_from_key(rarity_key)}` entfernt.",
                ephemeral=True,
            )
            return

        if action == "add_user":
            await module.add_give_op_user(interaction.guild.id, target_user_id)
            await interaction.followup.send(
                f"✅ {target_name} darf jetzt `/op-verwaltung` nutzen.",
                ephemeral=True,
            )
            return

        if action == "remove_user":
            await module.remove_give_op_user(interaction.guild.id, target_user_id)
            await interaction.followup.send(
                f"✅ {target_name} wurde aus `/op-verwaltung` entfernt.",
                ephemeral=True,
            )
            return

        if action in {"add_role", "remove_role"}:
            role_select_view = module.GiveOpRoleSelectView(interaction.user.id)
            await interaction.followup.send("Wähle die Rolle:", view=role_select_view, ephemeral=True)
            await role_select_view.wait()
            selected_role_id = role_select_view.value
            if not selected_role_id:
                await interaction.followup.send("⏰ Keine Rolle gewählt. Abgebrochen.", ephemeral=True)
                return
            role_obj = interaction.guild.get_role(selected_role_id)
            role_name = role_obj.mention if role_obj else f"`{selected_role_id}`"
            if action == "add_role":
                await module.add_give_op_role(interaction.guild.id, selected_role_id)
                await interaction.followup.send(
                    f"✅ Rolle {role_name} darf jetzt `/op-verwaltung` nutzen.",
                    ephemeral=True,
                )
                return
            await module.remove_give_op_role(interaction.guild.id, selected_role_id)
            await interaction.followup.send(
                f"✅ Rolle {role_name} wurde aus `/op-verwaltung` entfernt.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("❌ Unbekannte Aktion. Abgebrochen.", ephemeral=True)

    @bot.tree.command(name="entwicklerpanel", description="Nur für Admins!!!")
    async def panel(interaction: discord.Interaction):
        if not await module.require_owner_or_dev(interaction):
            return
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        embed = discord.Embed(title="Panel", description="Hauptmenü")
        await module._send_with_visibility(
            interaction,
            visibility_key,
            embed=embed,
            view=module.PanelHomeView(interaction.user.id),
        )

    balance_group = app_commands.Group(name="statistik", description="Balance-Statistiken")

    @balance_group.command(name="balance", description="Zeigt Balance-Statistiken")
    async def balance_stats(interaction: discord.Interaction):
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module.send_balance_stats(interaction, visibility_key=visibility_key)

    bot.tree.add_command(balance_group)

    @bot.tree.command(name="bot-status", description="Nur für Admins!!!")
    async def bot_status(interaction: discord.Interaction):
        if not await module.require_owner_or_dev(interaction):
            return
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module.send_bot_status(interaction, visibility_key=visibility_key)

    return {
        "configure_group": configure_group,
        "add_channel_shortcut": add_channel_shortcut,
        "configure_add": configure_add,
        "configure_remove": configure_remove,
        "configure_list": configure_list,
        "reset_intro": reset_intro,
        "vaultlook": vaultlook,
        "test_bericht": test_bericht,
        "give": give,
        "give_op": give_op,
        "panel": panel,
        "BALANCE_GROUP": balance_group,
        "balance_stats": balance_stats,
        "bot_status": bot_status,
    }
