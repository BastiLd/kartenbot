from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandApi:
    """
    Explicit object passed to command registrars.

    This replaces the old `sys.modules[__name__]` pattern and makes the command
    dependencies explicit and testable.
    """

    # We intentionally keep these as `Any` for now because the repo uses runtime
    # duck-typing heavily and the next step is to tighten types gradually.
    _items: dict[str, Any]

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - delegation
        try:
            return self._items[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def build_command_api(global_ns: dict[str, Any]) -> CommandApi:
    # This list is derived from the attributes used in:
    # - botcommands/player_commands.py
    # - botcommands/gameplay_commands.py
    # - botcommands/admin_commands.py
    required_names = {
        # player_commands.py
        "ALPHA_PHASE_ENABLED",
        "AnfangView",
        "DustAmountView",
        "FUSE_DUST_COST",
        "FUSE_HEALTH_BONUS",
        "InviteUserSelectView",
        "SPECIAL_DAMAGE_UPGRADE_MAX_TIMES",
        "SPECIAL_DAMAGE_UPGRADE_STEP",
        "STANDARD_DAMAGE_UPGRADE_MAX_TIMES",
        "STANDARD_DAMAGE_UPGRADE_STEP",
        "VISIBILITY_PRIVATE",
        "VISIBILITY_PUBLIC",
        "VaultView",
        "_card_by_name_local",
        "_card_name_ansi_block",
        "_card_rarity_color",
        "_group_option_label",
        "_group_owned_cards_for_current_mode",
        "_send_ephemeral",
        "_send_with_visibility",
        "build_anfang_intro_text",
        "check_and_add_karte",
        "command_visibility_key_for_interaction",
        "db_context",
        "get_infinitydust",
        "get_item_by_id",
        "get_invite_max_member_age_days",
        "get_karte_by_name",
        "get_latest_anfang_message",
        "get_message_visibility",
        "get_user_karten",
        "is_alpha_enabled",
        "is_beta_enabled",
        "is_admin",
        "is_channel_allowed",
        "karten",
        "random_gameplay_card",
        "set_latest_anfang_message",
        "set_invite_max_member_age_days",
        "time",
        # gameplay_commands.py
        "BattleView",
        "BETA_STORY_DISABLED_TEXT",
        "BETA_INVITE_DISABLED_TEXT",
        "CardSelectView",
        "ChallengeResponseView",
        "MissionAcceptView",
        "OpponentSelectView",
        "StoryPlayerView",
        "StorySelectView",
        "_build_attack_info_lines",
        "_build_mission_embed",
        "_create_required_private_fight_thread",
        "_create_required_private_mission_thread",
        "_fight_challenge_prompt",
        "_maybe_delete_fight_thread",
        "_safe_send_channel",
        "build_mission_from_operation",
        "build_operation_broken_timeline_mission",
        "claim_fight_request",
        "create_battle_embed",
        "create_battle_log_embed",
        "create_fight_request",
        "create_mission_request",
        "get_mission_count",
        "mission_operation_options",
        "update_fight_request_message",
        "update_mission_request_message",
        # admin_commands.py
        "AdminUserSelectView",
        "CardSelectPagerView",
        "CardVariantSelectView",
        "DustMultiUserSelectView",
        "GiveCardSelectView",
        "GiveOpActionView",
        "GiveOpRaritySelectView",
        "GiveOpRoleSelectView",
        "InfinitydustAmountView",
        "PanelHomeView",
        "SingleMultiModeView",
        "_cards_by_rarity_group",
        "_rarity_label_from_key",
        "add_exact_card_variant_once",
        "add_give_op_role",
        "add_give_op_user",
        "add_infinitydust",
        "add_karte_amount",
        "card_has_multiple_variants",
        "default_variant_name_for_base",
        "has_exact_card_variant",
        "is_config_admin",
        "remove_give_op_role",
        "remove_give_op_user",
        "remove_karte_amount",
        "require_owner_or_dev",
        "run_dust_command_flow",
        "send_balance_stats",
        "send_bot_status",
        "send_reset_intro",
        "send_test_report",
        "send_vaultlook",
        "variant_names_for_base",
    }

    items: dict[str, Any] = {}
    missing: list[str] = []
    for name in sorted(required_names):
        if name in global_ns:
            items[name] = global_ns[name]
        else:
            missing.append(name)

    if missing:
        # Keep it explicit and actionable.
        joined = ", ".join(missing)
        raise RuntimeError(f"CommandApi is missing required names: {joined}")

    return CommandApi(_items=items)

