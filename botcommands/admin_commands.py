from __future__ import annotations

from types import ModuleType

import discord
from discord import app_commands


def register_admin_commands(bot, module: ModuleType) -> dict[str, object]:
    configure_group = app_commands.Group(name="konfigurieren", description="Nur f\u00fcr Admins!!!")

    def _target_label(guild: discord.Guild | None, user_id: int) -> str:
        member = guild.get_member(int(user_id)) if guild is not None else None
        return member.mention if member is not None else f"`{int(user_id)}`"

    def _target_summary(guild: discord.Guild | None, user_ids: list[int]) -> str:
        if not user_ids:
            return "niemanden"
        if len(user_ids) <= 5:
            return ", ".join(_target_label(guild, user_id) for user_id in user_ids)
        return f"{len(user_ids)} Nutzer"

    async def _select_target_user_ids(
        interaction: discord.Interaction,
        *,
        placeholder: str,
        item_label: str,
    ) -> list[int] | None:
        if interaction.guild is None:
            return None
        mode_view = module.SingleMultiModeView(interaction.user.id, placeholder=placeholder)
        await interaction.followup.send("W\u00e4hle den Modus:", view=mode_view, ephemeral=True)
        await mode_view.wait()
        if not mode_view.value:
            return None
        if mode_view.value == "single":
            user_select_view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
            await interaction.followup.send("W\u00e4hle den Ziel-Nutzer:", view=user_select_view, ephemeral=True)
            await user_select_view.wait()
            if not user_select_view.value:
                return None
            return [int(user_select_view.value)]

        multi_view = module.DustMultiUserSelectView(interaction.user.id, interaction.guild, item_label=item_label)
        multi_message = await interaction.followup.send(
            content=multi_view._content(),
            embed=multi_view._summary_embed(),
            view=multi_view,
            ephemeral=True,
            wait=True,
        )
        multi_view.bind_message(multi_message)
        await multi_view.wait()
        if not multi_view.value:
            return None
        return [int(user_id) for user_id in multi_view.value]

    async def _select_exact_card_name(
        interaction: discord.Interaction,
        *,
        requester_id: int,
        include_infinitydust: bool = False,
    ) -> str | None:
        if include_infinitydust:
            card_select_view = module.GiveCardSelectView(requester_id, requester_id)
            prompt = "W\u00e4hle eine Karte oder Infinitydust:"
        else:
            card_select_view = module.CardSelectPagerView(requester_id, module.karten)
            prompt = "W\u00e4hle einen Helden:"
        await interaction.followup.send(prompt, view=card_select_view, ephemeral=True)
        await card_select_view.wait()
        if not card_select_view.value:
            return None
        selected_value = str(card_select_view.value or "").strip()
        if include_infinitydust and selected_value == "infinitydust":
            return selected_value
        if module.card_has_multiple_variants(selected_value, cards=module.karten):
            variant_rows = [
                (variant_name, 1)
                for variant_name in module.variant_names_for_base(selected_value, cards=module.karten)
            ]
            variant_view = module.CardVariantSelectView(
                requester_id,
                selected_value,
                variant_rows,
                placeholder=f"W\u00e4hle den Style f\u00fcr {selected_value}...",
            )
            await interaction.followup.send(
                f"W\u00e4hle den Style f\u00fcr **{selected_value}**:",
                view=variant_view,
                ephemeral=True,
            )
            await variant_view.wait()
            return str(variant_view.value or "").strip() or None
        return module.default_variant_name_for_base(selected_value, cards=module.karten)

    @bot.tree.command(name="kanal-freigeben", description="Nur f\u00fcr Admins!!!")
    async def add_channel_shortcut(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        if not interaction.guild_id or not interaction.channel_id:
            await module._send_ephemeral(
                interaction,
                content="\u274c Dieser Command funktioniert nur in einem Server-Kanal.",
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
            content=f"\u2705 Hinzugef\u00fcgt: {getattr(interaction.channel, "mention", "#unbekannt")}",
        )

    @configure_group.command(name="hinzufuegen", description="Nur f\u00fcr Admins!!!")
    async def configure_add(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("\u274c Keine Berechtigung.", ephemeral=True)
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
            content=f"\u2705 Hinzugef\u00fcgt: {getattr(interaction.channel, "mention", "#unbekannt")}",
        )

    @configure_group.command(name="entfernen", description="Nur f\u00fcr Admins!!!")
    async def configure_remove(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("\u274c Keine Berechtigung.", ephemeral=True)
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
            content=f"\U0001F5D1\ufe0f Entfernt: {getattr(interaction.channel, "mention", "#unbekannt")}",
        )

    @configure_group.command(name="liste", description="Nur f\u00fcr Admins!!!")
    async def configure_list(interaction: discord.Interaction):
        if not await module.is_config_admin(interaction):
            await interaction.response.send_message("\u274c Keine Berechtigung.", ephemeral=True)
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
                content="\u2139\ufe0f Es sind noch keine Kan\u00e4le erlaubt. Nutze `/konfigurieren hinzufuegen` im gew\u00fcnschten Kanal.",
            )
            return
        mentions = "\n".join(f"- <#{row[0]}>" for row in rows)
        await module._send_with_visibility(
            interaction,
            visibility_key,
            content=f"\u2705 Erlaubte Kan\u00e4le:\n{mentions}",
        )

    bot.tree.add_command(configure_group)

    @bot.tree.command(name="intro-zuruecksetzen", description="Nur f\u00fcr Admins!!!")
    async def reset_intro(interaction: discord.Interaction):
        if not await module.is_admin(interaction):
            await interaction.response.send_message("\u274c Keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module.send_reset_intro(interaction, visibility_key=visibility_key)

    @bot.tree.command(name="sammlung-ansehen", description="Nur f\u00fcr Admins!!!")
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
                "\u274c Du hast keine Berechtigung f\u00fcr diesen Command! Nur Admins k\u00f6nnen in andere Vaults schauen.",
                ephemeral=True,
            )
            return

        view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
        await interaction.followup.send(
            "W\u00e4hle einen User, dessen Vault du ansehen m\u00f6chtest:",
            view=view,
            ephemeral=ephemeral,
        )
        await view.wait()

        if not view.value:
            await interaction.followup.send("\u23f0 Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
            return

        target_user_id = int(view.value)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("\u274c Dieser Command funktioniert nur auf einem Server.", ephemeral=True)
            return
        target_user = guild.get_member(target_user_id)
        if not target_user:
            await interaction.followup.send("\u274c Nutzer nicht gefunden!", ephemeral=True)
            return

        await module.send_vaultlook(
            interaction,
            target_user_id,
            target_user.display_name,
            visibility_key=visibility_key,
        )

    @bot.tree.command(name="test-bericht", description="Nur f\u00fcr Admins!!!")
    async def test_bericht(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message("\u274c Du hast keine Berechtigung.", ephemeral=True)
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        await module._send_ephemeral(interaction, content="\U0001F50D Sammle verf\u00fcgbare Commands...")
        await module.send_test_report(interaction, visibility_key=visibility_key)

    @bot.tree.command(name="karte-geben", description="Nur f\u00fcr Admins!!!")
    async def give(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message(
                "\u274c Du hast keine Berechtigung f\u00fcr diesen Command! Nur Admins/Owner k\u00f6nnen Karten geben.",
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
            content="W\u00e4hle einen Nutzer, dem du eine Karte geben m\u00f6chtest:",
            view=user_select_view,
        )
        await user_select_view.wait()

        if not user_select_view.value:
            await interaction.followup.send("\u23f0 Keine Auswahl getroffen. Abgebrochen.", ephemeral=ephemeral)
            return

        target_user_id = int(user_select_view.value)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("\u274c Dieser Command funktioniert nur auf einem Server.", ephemeral=True)
            return
        target_user = guild.get_member(target_user_id)
        if not target_user:
            await interaction.followup.send("\u274c Nutzer nicht gefunden!", ephemeral=True)
            return

        selected_card_name = await _select_exact_card_name(
            interaction,
            requester_id=interaction.user.id,
            include_infinitydust=True,
        )
        if not selected_card_name:
            await interaction.followup.send("\u23f0 Keine Karte gew\u00e4hlt. Abgebrochen.", ephemeral=ephemeral)
            return

        if selected_card_name == "infinitydust":
            amount_view = module.InfinitydustAmountView(interaction.user.id, target_user_id)
            await interaction.followup.send(
                f"W\u00e4hle die Menge Infinitydust f\u00fcr {target_user.mention}:",
                view=amount_view,
                ephemeral=ephemeral,
            )
            await amount_view.wait()

            if not amount_view.value:
                await interaction.followup.send("\u23f0 Keine Menge gew\u00e4hlt. Abgebrochen.", ephemeral=ephemeral)
                return

            amount = amount_view.value
            await module.add_infinitydust(target_user_id, amount)

            embed = discord.Embed(
                title="\U0001F48E Infinitydust verschenkt!",
                description=f"{interaction.user.mention} hat **{amount}x Infinitydust** an {target_user.mention} gegeben!",
            )
            embed.set_thumbnail(url="https://i.imgur.com/L9v5mNI.png")
            await module._send_with_visibility(interaction, visibility_key, embed=embed)
            return

        selected_card = await module.get_karte_by_name(selected_card_name)
        was_added = await module.add_exact_card_variant_once(target_user_id, selected_card_name)
        selected_color = (
            module._card_rarity_color(selected_card)
            if selected_card
            else module._card_rarity_color(module._card_by_name_local(selected_card_name))
        )

        if was_added:
            embed = discord.Embed(
                title="\U0001F381 Karte verschenkt!",
                description=f"{interaction.user.mention} hat **{selected_card_name}** an {target_user.mention} gegeben!",
                color=selected_color,
            )
            if selected_card:
                embed.set_image(url=selected_card["bild"])
            await module._send_with_visibility(interaction, visibility_key, embed=embed)
            return

        embed = discord.Embed(
            title="\u26a0\ufe0f Variante bereits vorhanden",
            description=f"{target_user.mention} besitzt **{selected_card_name}** bereits.",
            color=selected_color,
        )
        await module._send_with_visibility(interaction, visibility_key, embed=embed)

    @bot.tree.command(name="dust", description="Nur f\u00fcr Admins!!!")
    @app_commands.describe(modus="Single f\u00fcr einen Nutzer oder Multi f\u00fcr mehrere Nutzer")
    @app_commands.choices(
        modus=[
            app_commands.Choice(name="single", value="single"),
            app_commands.Choice(name="multi", value="multi"),
        ]
    )
    async def dust(interaction: discord.Interaction, modus: str):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message(
                "\u274c Du hast keine Berechtigung f\u00fcr diesen Command! Nur Admins/Owner k\u00f6nnen Infinitydust vergeben.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await module.run_dust_command_flow(interaction, mode=modus, remove=False)

    @bot.tree.command(name="l\u00f6dust", description="Nur f\u00fcr Admins!!!")
    @app_commands.describe(modus="Single f\u00fcr einen Nutzer oder Multi f\u00fcr mehrere Nutzer")
    @app_commands.choices(
        modus=[
            app_commands.Choice(name="single", value="single"),
            app_commands.Choice(name="multi", value="multi"),
        ]
    )
    async def loedust(interaction: discord.Interaction, modus: str):
        if not await module.is_channel_allowed(interaction):
            return
        if not await module.is_admin(interaction):
            await interaction.response.send_message(
                "\u274c Du hast keine Berechtigung f\u00fcr diesen Command! Nur Admins/Owner k\u00f6nnen Infinitydust entfernen.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await module.run_dust_command_flow(interaction, mode=modus, remove=True)

    @bot.tree.command(name="op-verwaltung", description="Nur f\u00fcr Admins!!!")
    @app_commands.guild_only()
    async def give_op(interaction: discord.Interaction):
        if not await module.is_channel_allowed(interaction):
            return
        if interaction.guild is None:
            await module._send_ephemeral(interaction, content="Dieser Command ist nur in Servern verf\u00fcgbar.")
            return
        if not await module.is_admin(interaction):
            await module._send_ephemeral(
                interaction,
                content="\u274c Du hast keine Berechtigung f\u00fcr diesen Command. Nur Admins k\u00f6nnen `/op-verwaltung` nutzen.",
            )
            return

        action_view = module.GiveOpActionView(interaction.user.id)
        await module._send_ephemeral(
            interaction,
            content=(
                "W\u00e4hle eine Aktion:\n"
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
            await interaction.followup.send("\u23f0 Keine Aktion gew\u00e4hlt. Abgebrochen.", ephemeral=True)
            return

        selected_user_ids: list[int] = []
        if action in {"group_give", "group_remove", "add_user", "remove_user"}:
            user_select_view = module.AdminUserSelectView(interaction.user.id, interaction.guild)
            await interaction.followup.send("W\u00e4hle den Ziel-Nutzer:", view=user_select_view, ephemeral=True)
            await user_select_view.wait()
            if not user_select_view.value:
                await interaction.followup.send("\u23f0 Keine Nutzer-Auswahl. Abgebrochen.", ephemeral=True)
                return
            target_user_id = int(user_select_view.value)
            selected_user_ids = [target_user_id]
            target_name = _target_label(interaction.guild, target_user_id)
        elif action in {"card_give", "card_remove"}:
            selected_user_ids = await _select_target_user_ids(
                interaction,
                placeholder="W\u00e4hle Single oder Multi...",
                item_label="Karten",
            ) or []
            if not selected_user_ids:
                await interaction.followup.send("\u23f0 Keine Nutzer-Auswahl. Abgebrochen.", ephemeral=True)
                return
            target_user_id = selected_user_ids[0]
            target_name = _target_summary(interaction.guild, selected_user_ids)
        else:
            target_user_id = 0
            target_name = ""

        if action == "card_give":
            card_name = await _select_exact_card_name(interaction, requester_id=interaction.user.id)
            if not card_name:
                await interaction.followup.send("\u23f0 Keine Karte gew\u00e4hlt. Abgebrochen.", ephemeral=True)
                return
            added_user_ids: list[int] = []
            skipped_user_ids: list[int] = []
            for current_user_id in selected_user_ids:
                was_added = await module.add_exact_card_variant_once(current_user_id, card_name)
                if was_added:
                    added_user_ids.append(current_user_id)
                else:
                    skipped_user_ids.append(current_user_id)
            summary_lines: list[str] = []
            if added_user_ids:
                summary_lines.append(f"\u2705 `{card_name}` gegeben an {_target_summary(interaction.guild, added_user_ids)}.")
            if skipped_user_ids:
                summary_lines.append(f"\u26a0\ufe0f Bereits vorhanden bei {_target_summary(interaction.guild, skipped_user_ids)}.")
            await interaction.followup.send(
                "\n".join(summary_lines) or "\u26a0\ufe0f Keine \u00c4nderungen durchgef\u00fchrt.",
                ephemeral=True,
            )
            return

        if action == "card_remove":
            card_name = await _select_exact_card_name(interaction, requester_id=interaction.user.id)
            if not card_name:
                await interaction.followup.send("\u23f0 Keine Karte gew\u00e4hlt. Abgebrochen.", ephemeral=True)
                return
            removed_user_ids: list[int] = []
            missing_user_ids: list[int] = []
            for current_user_id in selected_user_ids:
                if not await module.has_exact_card_variant(current_user_id, card_name):
                    missing_user_ids.append(current_user_id)
                    continue
                await module.remove_karte_amount(current_user_id, card_name, 1)
                removed_user_ids.append(current_user_id)
            remove_summary_lines: list[str] = []
            if removed_user_ids:
                remove_summary_lines.append(f"\u2705 `{card_name}` entfernt bei {_target_summary(interaction.guild, removed_user_ids)}.")
            if missing_user_ids:
                remove_summary_lines.append(f"\u26a0\ufe0f Nicht vorhanden bei {_target_summary(interaction.guild, missing_user_ids)}.")
            await interaction.followup.send(
                "\n".join(remove_summary_lines) or "\u26a0\ufe0f Keine \u00c4nderungen durchgef\u00fchrt.",
                ephemeral=True,
            )
            return

        if action in {"group_give", "group_remove"}:
            grouped_cards = module._cards_by_rarity_group()
            rarity_keys = sorted(grouped_cards.keys())
            rarity_view = module.GiveOpRaritySelectView(interaction.user.id, rarity_keys)
            await interaction.followup.send(
                "W\u00e4hle die Karten-Gruppe (Seltenheit):",
                view=rarity_view,
                ephemeral=True,
            )
            await rarity_view.wait()
            rarity_key = rarity_view.value
            if not rarity_key:
                await interaction.followup.send(
                    "\u23f0 Keine Gruppe gew\u00e4hlt. Abgebrochen.",
                    ephemeral=True,
                )
                return
            cards_for_group = grouped_cards.get(rarity_key, [])
            if not cards_for_group:
                await interaction.followup.send(
                    "\u274c F\u00fcr diese Gruppe wurden keine Karten gefunden.",
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
                    f"\u2705 {target_name} hat {changed_count} Karte(n) aus `{module._rarity_label_from_key(rarity_key)}` erhalten.",
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
                f"\u2705 {target_name}: {changed_count} Karte(n) aus `{module._rarity_label_from_key(rarity_key)}` entfernt.",
                ephemeral=True,
            )
            return

        if action == "add_user":
            await module.add_give_op_user(interaction.guild.id, target_user_id)
            await interaction.followup.send(
                f"\u2705 {target_name} darf jetzt `/op-verwaltung` nutzen.",
                ephemeral=True,
            )
            return

        if action == "remove_user":
            await module.remove_give_op_user(interaction.guild.id, target_user_id)
            await interaction.followup.send(
                f"\u2705 {target_name} wurde aus `/op-verwaltung` entfernt.",
                ephemeral=True,
            )
            return

        if action in {"add_role", "remove_role"}:
            role_select_view = module.GiveOpRoleSelectView(interaction.user.id)
            await interaction.followup.send("W\u00e4hle die Rolle:", view=role_select_view, ephemeral=True)
            await role_select_view.wait()
            selected_role_id = role_select_view.value
            if not selected_role_id:
                await interaction.followup.send(
                    "\u23f0 Keine Rolle gew\u00e4hlt. Abgebrochen.",
                    ephemeral=True,
                )
                return
            role_obj = interaction.guild.get_role(selected_role_id)
            role_name = role_obj.mention if role_obj else f"`{selected_role_id}`"
            if action == "add_role":
                await module.add_give_op_role(interaction.guild.id, selected_role_id)
                await interaction.followup.send(
                    f"\u2705 Rolle {role_name} darf jetzt `/op-verwaltung` nutzen.",
                    ephemeral=True,
                )
                return
            await module.remove_give_op_role(interaction.guild.id, selected_role_id)
            await interaction.followup.send(
                f"\u2705 Rolle {role_name} wurde aus `/op-verwaltung` entfernt.",
                ephemeral=True,
            )
            return

        await interaction.followup.send("\u274c Unbekannte Aktion. Abgebrochen.", ephemeral=True)

    @bot.tree.command(name="entwicklerpanel", description="Nur f\u00fcr Admins!!!")
    async def panel(interaction: discord.Interaction):
        if not await module.require_owner_or_dev(interaction):
            return
        if not await module.is_channel_allowed(interaction):
            return
        visibility_key = module.command_visibility_key_for_interaction(interaction)
        embed = discord.Embed(title="Panel", description="Hauptmen?")
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

    @bot.tree.command(name="bot-status", description="Nur f\u00fcr Admins!!!")
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
        "dust": dust,
        "loedust": loedust,
        "give_op": give_op,
        "panel": panel,
        "BALANCE_GROUP": balance_group,
        "balance_stats": balance_stats,
        "bot_status": bot_status,
    }
