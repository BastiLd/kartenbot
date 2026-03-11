import discord
from discord import SelectOption, ui


def _resolve_requester_id(requester) -> int:
    try:
        return int(requester.id)
    except AttributeError:
        return int(requester)


def _presence_priority(member) -> int:
    status = getattr(member, "status", discord.Status.offline)
    if status == discord.Status.online:
        return 0
    if status == discord.Status.idle:
        return 1
    if status == discord.Status.dnd:
        return 2
    return 3


def _status_circle(member) -> str:
    status = getattr(member, "status", discord.Status.offline)
    if status == discord.Status.online:
        return "🟢"
    if status == discord.Status.idle:
        return "🟡"
    if status == discord.Status.dnd:
        return "🔴"
    return "⚫"


class RestrictedView(ui.View):
    def __init__(self, *args, interaction_checker=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._interaction_checker = interaction_checker

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._interaction_checker is None:
            return True
        return await self._interaction_checker(interaction)


class RestrictedModal(ui.Modal):
    def __init__(self, *args, interaction_checker=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._interaction_checker = interaction_checker

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._interaction_checker is None:
            return True
        return await self._interaction_checker(interaction)


class ShowAllMembersPager(ui.View):
    def __init__(self, requester, members, parent_view: ui.View | None = None, include_bot_option: bool = False):
        super().__init__(timeout=120)
        self.requester = requester
        self.parent_view = parent_view
        self.include_bot_option = include_bot_option
        self.members = [member for member in members if not getattr(member, "bot", False)]
        self.sorted_members = sorted(self.members, key=_presence_priority)

        self.pages = []
        remaining = list(self.sorted_members)
        first_cap = 24 if self.include_bot_option else 25
        if remaining or self.include_bot_option:
            self.pages.append(remaining[:first_cap])
            remaining = remaining[first_cap:]
        while remaining:
            self.pages.append(remaining[:25])
            remaining = remaining[25:]
        if not self.pages:
            self.pages = [[]]
        self.page_index = 0

        self.select = ui.Select(
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options_for_current_page(),
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.prev_btn = ui.Button(label="Zurück", style=discord.ButtonStyle.secondary, disabled=True)
        self.next_btn = ui.Button(label="Weiter", style=discord.ButtonStyle.secondary, disabled=(len(self.pages) <= 1))
        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def _placeholder(self) -> str:
        return f"Seite {self.page_index + 1}/{len(self.pages)} – Nutzer wählen..."

    def _build_options_for_current_page(self) -> list[SelectOption]:
        options: list[SelectOption] = []
        if self.include_bot_option and self.page_index == 0:
            options.append(SelectOption(label="🤖 Bot", value="bot"))
        for member in self.pages[self.page_index]:
            label = f"{_status_circle(member)} {str(getattr(member, 'display_name', 'Unbekannt'))[:100]}"
            options.append(SelectOption(label=label, value=str(getattr(member, "id"))))
        if not options:
            options.append(SelectOption(label="Keine Nutzer verfügbar", value="none"))
        return options

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != _resolve_requester_id(self.requester):
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return

        choice = self.select.values[0]
        if choice == "none":
            await interaction.response.send_message("❌ Keine Nutzer verfügbar!", ephemeral=True)
            return

        if self.parent_view is not None:
            try:
                self.parent_view.value = choice
                self.parent_view.stop()
            except Exception:
                import logging

                logging.exception("Unexpected error")
        self.stop()
        await interaction.response.defer()

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != _resolve_requester_id(self.requester):
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index > 0:
            self.page_index -= 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = self.page_index == 0
            self.next_btn.disabled = self.page_index == len(self.pages) - 1
            await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != _resolve_requester_id(self.requester):
            await interaction.response.send_message("Nicht dein Menü!", ephemeral=True)
            return
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
            self.select.options = self._build_options_for_current_page()
            self.select.placeholder = self._placeholder()
            self.prev_btn.disabled = self.page_index == 0
            self.next_btn.disabled = self.page_index == len(self.pages) - 1
            await interaction.response.edit_message(view=self)
