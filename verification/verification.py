from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import discord
from discord.ext import tasks

from redbot.core import Config, commands, modlog as core_modlog
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list

from .captcha import generate_code, render_captcha
from .views import CaptchaModal, VerificationPanel

log = logging.getLogger("red.4cd_cogs.verification")


class Verification(commands.Cog):
    """Role-gated CAPTCHA verification for new members."""

    __version__ = "1.0.0"

    DEFAULT_GUILD: dict[str, Any] = {
        "enabled": False,
        "verification_channel_id": None,
        "panel_message_id": None,
        "pending_role_ids": [],
        "verified_role_ids": [],
        "captcha_code_length": 6,
        "captcha_timeout_seconds": 600,
        "captcha_max_attempts": 3,
        "attempts_exhausted_action": "none",
        "account_age_min_hours": 0,
        "account_age_action": "none",
        "raid_enabled": False,
        "raid_code_length": None,
        "raid_timeout_seconds": None,
        "raid_max_attempts": None,
        "raid_attempts_exhausted_action": None,
        "raid_account_age_min_hours": None,
        "raid_account_age_action": "require",
        "pending_max_seconds": 0,
        "pending_expiry_action": "none",
        "pending_timeout_minutes": 60,
    }

    DEFAULT_MEMBER: dict[str, Any] = {
        "captcha_code": None,
        "captcha_attempts": 0,
        "captcha_expires_at": 0,
        "pending_since": 0,
        "pending_expired_handled": False,
    }

    CASETYPES: tuple[dict[str, str], ...] = (
        {"name": "Verification Success", "type": "verification_success", "emoji": "✅"},
        {"name": "Verification Failure", "type": "verification_failure", "emoji": "⚠️"},
        {"name": "Verification Expired", "type": "verification_expired", "emoji": "⏳"},
        {"name": "Verification Rejected", "type": "verification_rejected", "emoji": "🚫"},
    )

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4635210973123, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD)
        self.config.register_member(**self.DEFAULT_MEMBER)
        self.panel_view = VerificationPanel(self)
        self._lock = asyncio.Lock()

    async def cog_load(self) -> None:
        self.bot.add_view(self.panel_view)
        await self._register_casetypes()
        self.expiry_sweep.start()

    async def cog_unload(self) -> None:
        self.expiry_sweep.cancel()

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        del requester
        all_members = await self.config.all_members()
        for guild_id, members in all_members.items():
            if int(user_id) in members:
                await self.config.member_from_ids(int(guild_id), int(user_id)).clear()

    async def _register_casetypes(self) -> None:
        for case in self.CASETYPES:
            try:
                await core_modlog.register_casetype(
                    name=case["type"],
                    default_setting=True,
                    image=case["emoji"],
                    case_str=case["name"],
                )
            except RuntimeError:
                continue
            except Exception:
                log.debug("Failed to register casetype %s", case["type"], exc_info=True)

    async def _record_case(self, member: discord.Member, action_type: str, reason: str) -> None:
        me = member.guild.me or member.guild.get_member(self.bot.user.id) if self.bot.user else None
        if me is None:
            return
        try:
            await core_modlog.create_case(
                self.bot,
                member.guild,
                datetime.now(UTC),
                action_type,
                member,
                moderator=me,
                reason=reason,
            )
        except (RuntimeError, ValueError, discord.HTTPException, AttributeError):
            return

    def _active_policy(self, conf: dict[str, Any]) -> dict[str, Any]:
        code_length = conf["captcha_code_length"]
        timeout = conf["captcha_timeout_seconds"]
        attempts = conf["captcha_max_attempts"]
        attempts_action = conf["attempts_exhausted_action"]
        min_age = conf["account_age_min_hours"]
        age_action = conf["account_age_action"]

        if conf["raid_enabled"]:
            if conf["raid_code_length"] is not None:
                code_length = conf["raid_code_length"]
            if conf["raid_timeout_seconds"] is not None:
                timeout = conf["raid_timeout_seconds"]
            if conf["raid_max_attempts"] is not None:
                attempts = conf["raid_max_attempts"]
            if conf["raid_attempts_exhausted_action"] is not None:
                attempts_action = conf["raid_attempts_exhausted_action"]
            if conf["raid_account_age_min_hours"] is not None:
                min_age = conf["raid_account_age_min_hours"]
                age_action = conf["raid_account_age_action"]

        return {
            "code_length": max(4, min(10, int(code_length))),
            "timeout": max(60, int(timeout)),
            "attempts": max(1, int(attempts)),
            "attempts_action": attempts_action,
            "min_age_hours": max(0, int(min_age)),
            "age_action": age_action,
        }

    def _account_age_ok(self, member: discord.Member, min_age_hours: int) -> bool:
        if min_age_hours <= 0:
            return True
        age = datetime.now(UTC) - member.created_at
        return age >= timedelta(hours=min_age_hours)

    def _get_roles(self, guild: discord.Guild, role_ids: list[int]) -> list[discord.Role]:
        roles = []
        for role_id in role_ids:
            role = guild.get_role(int(role_id))
            if role is not None:
                roles.append(role)
        return roles

    def _manageable_roles(self, guild: discord.Guild, roles: list[discord.Role]) -> list[discord.Role]:
        me = guild.me
        if me is None or not guild.me.guild_permissions.manage_roles:
            return []
        return [r for r in roles if not r.managed and me.top_role > r]

    async def _apply_member_roles(
        self,
        member: discord.Member,
        *,
        add_role_ids: list[int],
        remove_role_ids: list[int],
        reason: str,
    ) -> None:
        add_roles = self._manageable_roles(member.guild, self._get_roles(member.guild, add_role_ids))
        remove_roles = self._manageable_roles(member.guild, self._get_roles(member.guild, remove_role_ids))

        if add_roles:
            try:
                await member.add_roles(*add_roles, reason=reason)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                log.debug("Failed adding verification roles for %s", member.id, exc_info=True)

        if remove_roles:
            try:
                await member.remove_roles(*remove_roles, reason=reason)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                log.debug("Failed removing verification roles for %s", member.id, exc_info=True)

    async def _set_pending(self, member: discord.Member, conf: dict[str, Any], *, reason: str) -> None:
        await self._apply_member_roles(
            member,
            add_role_ids=conf["pending_role_ids"],
            remove_role_ids=[],
            reason=reason,
        )
        await self.config.member(member).pending_since.set(int(datetime.now(UTC).timestamp()))
        await self.config.member(member).pending_expired_handled.set(False)

    async def _mark_verified(self, member: discord.Member, conf: dict[str, Any], *, reason: str) -> None:
        await self._apply_member_roles(
            member,
            add_role_ids=conf["verified_role_ids"],
            remove_role_ids=conf["pending_role_ids"],
            reason=reason,
        )
        await self.config.member(member).clear()

    async def _clear_captcha_state(self, member: discord.Member) -> None:
        await self.config.member(member).captcha_code.set(None)
        await self.config.member(member).captcha_attempts.set(0)
        await self.config.member(member).captcha_expires_at.set(0)

    async def _kick_member(self, member: discord.Member, reason: str) -> bool:
        try:
            await member.kick(reason=reason)
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return False

    async def _timeout_member(self, member: discord.Member, minutes: int, reason: str) -> bool:
        me = member.guild.me
        if me is None or not me.guild_permissions.moderate_members:
            return False
        try:
            until = datetime.now(UTC) + timedelta(minutes=max(1, minutes))
            await member.edit(timed_out_until=until, reason=reason)
            return True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return False

    async def _is_unverified(self, member: discord.Member, conf: dict[str, Any]) -> bool:
        pending_ids = set(int(x) for x in conf["pending_role_ids"])
        if pending_ids and any(r.id in pending_ids for r in member.roles):
            return True

        if conf["verified_role_ids"]:
            verified_ids = set(int(x) for x in conf["verified_role_ids"])
            return not any(r.id in verified_ids for r in member.roles)
        return True

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        conf = await self.config.guild(member.guild).all()
        if not conf["enabled"]:
            return

        await self._set_pending(member, conf, reason="Verification: joined server")
        policy = self._active_policy(conf)

        if policy["min_age_hours"] > 0 and not self._account_age_ok(member, policy["min_age_hours"]):
            if policy["age_action"] == "reject":
                kicked = await self._kick_member(
                    member,
                    reason=(
                        "Verification rejected: account too new "
                        f"(minimum {policy['min_age_hours']}h)."
                    ),
                )
                if kicked:
                    await self._record_case(
                        member,
                        "verification_rejected",
                        f"Auto-rejected due to account age below {policy['min_age_hours']} hours.",
                    )

    async def handle_panel_click(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            if not interaction.response.is_done():
                await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        conf = await self.config.guild(interaction.guild).all()
        if not conf["enabled"]:
            await interaction.response.send_message("Verification is currently disabled.", ephemeral=True)
            return

        configured_channel_id = conf["verification_channel_id"]
        if configured_channel_id and interaction.channel_id != int(configured_channel_id):
            channel = interaction.guild.get_channel(int(configured_channel_id))
            channel_name = channel.mention if channel else "the verification channel"
            await interaction.response.send_message(
                f"Please use {channel_name} to start verification.",
                ephemeral=True,
            )
            return

        if not await self._is_unverified(interaction.user, conf):
            await interaction.response.send_message("You are already verified.", ephemeral=True)
            return

        policy = self._active_policy(conf)
        if policy["min_age_hours"] > 0 and not self._account_age_ok(interaction.user, policy["min_age_hours"]):
            if policy["age_action"] == "reject":
                await interaction.response.send_message(
                    "Your account does not meet this server's minimum account age requirement.",
                    ephemeral=True,
                )
                return

        async with self._lock:
            member_conf = self.config.member(interaction.user)
            state = await member_conf.all()
            now = int(datetime.now(UTC).timestamp())
            expires_at = int(state["captcha_expires_at"])
            active_code = state["captcha_code"] if state["captcha_code"] and expires_at > now else None

            if active_code is None:
                active_code = generate_code(policy["code_length"])
                await member_conf.captcha_attempts.set(0)

            expiry = now + int(policy["timeout"])
            await member_conf.captcha_code.set(active_code)
            await member_conf.captcha_expires_at.set(expiry)

        file = discord.File(render_captcha(active_code), filename="verification_captcha.png")

        try:
            await interaction.user.send(
                content=(
                    "Complete verification by solving the attached CAPTCHA image. "
                    f"This challenge expires <t:{expiry}:R>."
                ),
                file=file,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't send you a DM. Please enable DMs from server members and try again.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "I couldn't send your CAPTCHA right now. Please try again in a moment.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CaptchaModal(self))

    async def handle_modal_submit(self, interaction: discord.Interaction, value: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            if not interaction.response.is_done():
                await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return

        conf = await self.config.guild(interaction.guild).all()
        policy = self._active_policy(conf)

        member_conf = self.config.member(interaction.user)
        state = await member_conf.all()
        now = int(datetime.now(UTC).timestamp())

        code = state["captcha_code"]
        expires_at = int(state["captcha_expires_at"])

        if not code or expires_at <= now:
            await self._clear_captcha_state(interaction.user)
            await interaction.response.send_message(
                "That verification challenge is stale or expired. Press Verify again for a new challenge.",
                ephemeral=True,
            )
            return

        provided = "".join(value.upper().split())
        if provided == str(code).upper():
            await self._mark_verified(
                interaction.user,
                conf,
                reason="Verification: CAPTCHA solved",
            )
            await self._record_case(interaction.user, "verification_success", "Solved CAPTCHA successfully.")
            await interaction.response.send_message(
                "✅ Verification complete. You now have access.",
                ephemeral=True,
            )
            return

        attempts = int(state["captcha_attempts"]) + 1
        await member_conf.captcha_attempts.set(attempts)

        remaining = int(policy["attempts"]) - attempts
        if remaining > 0:
            await interaction.response.send_message(
                f"Incorrect code. You have {remaining} attempt(s) remaining.",
                ephemeral=True,
            )
            return

        await self._clear_captcha_state(interaction.user)
        await self._record_case(
            interaction.user,
            "verification_failure",
            "Maximum CAPTCHA attempts exhausted.",
        )

        exhausted_action = str(policy["attempts_action"]).lower()
        if exhausted_action == "kick":
            kicked = await self._kick_member(interaction.user, "Verification failed: attempts exhausted")
            if kicked:
                await interaction.response.send_message(
                    "Verification failed and you have been removed from the server.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "Verification failed. Press Verify again to request a new challenge.",
            ephemeral=True,
        )

    @tasks.loop(minutes=2)
    async def expiry_sweep(self) -> None:
        now = int(datetime.now(UTC).timestamp())

        for guild in self.bot.guilds:
            conf = await self.config.guild(guild).all()
            if not conf["enabled"]:
                continue

            members = await self.config.all_members(guild)
            for member_id, state in members.items():
                member = guild.get_member(int(member_id))
                if member is None or member.bot:
                    continue

                expires_at = int(state.get("captcha_expires_at", 0) or 0)
                if expires_at and expires_at <= now:
                    await self._clear_captcha_state(member)

                pending_max = int(conf.get("pending_max_seconds", 0) or 0)
                if pending_max <= 0:
                    continue

                pending_since = int(state.get("pending_since", 0) or 0)
                already_handled = bool(state.get("pending_expired_handled", False))
                if pending_since <= 0 or already_handled:
                    continue

                if now - pending_since < pending_max:
                    continue

                action = str(conf.get("pending_expiry_action", "none")).lower()
                reason = "Verification pending status exceeded configured maximum duration"
                action_result = False

                if action == "kick":
                    action_result = await self._kick_member(member, reason)
                elif action == "timeout":
                    action_result = await self._timeout_member(
                        member,
                        int(conf.get("pending_timeout_minutes", 60) or 60),
                        reason,
                    )

                await self._record_case(
                    member,
                    "verification_expired",
                    f"Pending verification exceeded {pending_max} seconds; action={action}; applied={action_result}",
                )
                await self.config.member(member).pending_expired_handled.set(True)

    @expiry_sweep.before_loop
    async def _before_expiry_sweep(self) -> None:
        waiter = getattr(self.bot, "wait_until_red_ready", None)
        if waiter is not None:
            await waiter()
        else:
            await self.bot.wait_until_ready()

    @commands.group(name="verifyset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def verifyset(self, ctx: commands.Context) -> None:
        """Configure verification settings."""

    @verifyset.command(name="toggle")
    async def verifyset_toggle(self, ctx: commands.Context, enabled: bool) -> None:
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"Verification enabled: **{enabled}**")

    @verifyset.command(name="channel")
    async def verifyset_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).verification_channel_id.set(channel.id)
        await ctx.send(f"Verification channel set to {channel.mention}.")

    @verifyset.command(name="panel")
    async def verifyset_panel(self, ctx: commands.Context) -> None:
        conf = await self.config.guild(ctx.guild).all()
        channel_id = conf["verification_channel_id"]
        if not channel_id:
            await ctx.send("Set a verification channel first with `[p]verifyset channel`.")
            return

        channel = ctx.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Configured verification channel is invalid.")
            return

        embed = discord.Embed(
            title="Server Verification",
            description="Press **Verify** to receive a CAPTCHA challenge and unlock access.",
            color=discord.Color.green(),
        )
        msg = await channel.send(embed=embed, view=self.panel_view)
        await self.config.guild(ctx.guild).panel_message_id.set(msg.id)
        await ctx.send(f"Verification panel posted in {channel.mention}.")

    @verifyset.group(name="pendingrole")
    async def verifyset_pendingrole(self, ctx: commands.Context) -> None:
        """Manage pending roles."""

    @verifyset_pendingrole.command(name="add")
    async def verifyset_pendingrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).pending_role_ids() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"Added pending role: {role.mention}")

    @verifyset_pendingrole.command(name="remove")
    async def verifyset_pendingrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).pending_role_ids() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
        await ctx.send(f"Removed pending role: {role.mention}")

    @verifyset.group(name="verifiedrole")
    async def verifyset_verifiedrole(self, ctx: commands.Context) -> None:
        """Manage verified/access roles."""

    @verifyset_verifiedrole.command(name="add")
    async def verifyset_verifiedrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).verified_role_ids() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"Added verified role: {role.mention}")

    @verifyset_verifiedrole.command(name="remove")
    async def verifyset_verifiedrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        async with self.config.guild(ctx.guild).verified_role_ids() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
        await ctx.send(f"Removed verified role: {role.mention}")

    @verifyset.group(name="captcha")
    async def verifyset_captcha(self, ctx: commands.Context) -> None:
        """Configure captcha challenge behavior."""

    @verifyset_captcha.command(name="length")
    async def verifyset_captcha_length(self, ctx: commands.Context, length: commands.Range[int, 4, 10]) -> None:
        await self.config.guild(ctx.guild).captcha_code_length.set(int(length))
        await ctx.send(f"CAPTCHA code length set to **{length}**.")

    @verifyset_captcha.command(name="attempts")
    async def verifyset_captcha_attempts(self, ctx: commands.Context, attempts: commands.Range[int, 1, 10]) -> None:
        await self.config.guild(ctx.guild).captcha_max_attempts.set(int(attempts))
        await ctx.send(f"CAPTCHA maximum attempts set to **{attempts}**.")

    @verifyset_captcha.command(name="timeout")
    async def verifyset_captcha_timeout(self, ctx: commands.Context, seconds: commands.Range[int, 60, 3600]) -> None:
        await self.config.guild(ctx.guild).captcha_timeout_seconds.set(int(seconds))
        await ctx.send(f"CAPTCHA timeout set to **{seconds}** seconds.")

    @verifyset_captcha.command(name="onexhaust")
    async def verifyset_captcha_onexhaust(self, ctx: commands.Context, action: Literal["none", "kick"]) -> None:
        await self.config.guild(ctx.guild).attempts_exhausted_action.set(action)
        await ctx.send(f"Attempts-exhausted action set to **{action}**.")

    @verifyset.group(name="accountage")
    async def verifyset_accountage(self, ctx: commands.Context) -> None:
        """Configure account age protection."""

    @verifyset_accountage.command(name="set")
    async def verifyset_accountage_set(
        self,
        ctx: commands.Context,
        minimum_hours: commands.Range[int, 0, 8760],
        action: Literal["none", "require", "reject"],
    ) -> None:
        await self.config.guild(ctx.guild).account_age_min_hours.set(int(minimum_hours))
        await self.config.guild(ctx.guild).account_age_action.set(action)
        await ctx.send(
            f"Account age rule updated: minimum **{minimum_hours}h**, action **{action}**."
        )

    @verifyset.group(name="raid")
    async def verifyset_raid(self, ctx: commands.Context) -> None:
        """Configure raid mode and stricter override settings."""

    @verifyset_raid.command(name="enable")
    async def verifyset_raid_enable(self, ctx: commands.Context, enabled: bool) -> None:
        await self.config.guild(ctx.guild).raid_enabled.set(enabled)
        await ctx.send(f"Raid mode enabled: **{enabled}**")

    @verifyset_raid.command(name="attempts")
    async def verifyset_raid_attempts(
        self, ctx: commands.Context, attempts: commands.Range[int, 1, 10] | None = None
    ) -> None:
        await self.config.guild(ctx.guild).raid_max_attempts.set(int(attempts) if attempts else None)
        await ctx.send(
            "Raid attempts override set to "
            f"**{attempts if attempts is not None else 'default'}**."
        )

    @verifyset_raid.command(name="timeout")
    async def verifyset_raid_timeout(
        self, ctx: commands.Context, seconds: commands.Range[int, 60, 3600] | None = None
    ) -> None:
        await self.config.guild(ctx.guild).raid_timeout_seconds.set(int(seconds) if seconds else None)
        await ctx.send(
            "Raid timeout override set to "
            f"**{seconds if seconds is not None else 'default'}** seconds."
        )

    @verifyset_raid.command(name="length")
    async def verifyset_raid_length(
        self, ctx: commands.Context, length: commands.Range[int, 4, 10] | None = None
    ) -> None:
        await self.config.guild(ctx.guild).raid_code_length.set(int(length) if length else None)
        await ctx.send(
            "Raid code length override set to "
            f"**{length if length is not None else 'default'}**."
        )

    @verifyset_raid.command(name="accountage")
    async def verifyset_raid_accountage(
        self,
        ctx: commands.Context,
        minimum_hours: commands.Range[int, 0, 8760] | None = None,
        action: Literal["require", "reject"] = "require",
    ) -> None:
        await self.config.guild(ctx.guild).raid_account_age_min_hours.set(
            int(minimum_hours) if minimum_hours is not None else None
        )
        await self.config.guild(ctx.guild).raid_account_age_action.set(action)
        await ctx.send(
            "Raid account age override updated: "
            f"hours=**{minimum_hours if minimum_hours is not None else 'default'}**, "
            f"action=**{action}**."
        )

    @verifyset_raid.command(name="onexhaust")
    async def verifyset_raid_onexhaust(
        self, ctx: commands.Context, action: Literal["default", "none", "kick"] = "default"
    ) -> None:
        value: str | None = None if action == "default" else action
        await self.config.guild(ctx.guild).raid_attempts_exhausted_action.set(value)
        await ctx.send(f"Raid attempts-exhausted action override set to **{action}**.")

    @verifyset.group(name="pendingexpiry")
    async def verifyset_pendingexpiry(self, ctx: commands.Context) -> None:
        """Configure maximum pending/unverified duration and action."""

    @verifyset_pendingexpiry.command(name="set")
    async def verifyset_pendingexpiry_set(
        self,
        ctx: commands.Context,
        maximum_seconds: commands.Range[int, 0, 2592000],
        action: Literal["none", "kick", "timeout"] = "none",
        timeout_minutes: commands.Range[int, 1, 40320] = 60,
    ) -> None:
        await self.config.guild(ctx.guild).pending_max_seconds.set(int(maximum_seconds))
        await self.config.guild(ctx.guild).pending_expiry_action.set(action)
        await self.config.guild(ctx.guild).pending_timeout_minutes.set(int(timeout_minutes))
        await ctx.send(
            "Pending expiry policy updated: "
            f"max=**{maximum_seconds}s**, action=**{action}**, timeout=**{timeout_minutes}m**."
        )

    @verifyset.command(name="settings")
    async def verifyset_settings(self, ctx: commands.Context) -> None:
        conf = await self.config.guild(ctx.guild).all()
        policy = self._active_policy(conf)

        pending_roles = self._get_roles(ctx.guild, conf["pending_role_ids"])
        verified_roles = self._get_roles(ctx.guild, conf["verified_role_ids"])

        channel = ctx.guild.get_channel(conf["verification_channel_id"]) if conf["verification_channel_id"] else None

        embed = discord.Embed(title="Verification Settings", color=discord.Color.blurple())
        embed.add_field(name="Enabled", value=str(conf["enabled"]))
        embed.add_field(name="Raid mode", value=str(conf["raid_enabled"]))
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=False)
        embed.add_field(
            name="Pending roles",
            value=humanize_list([r.mention for r in pending_roles]) or "None",
            inline=False,
        )
        embed.add_field(
            name="Verified roles",
            value=humanize_list([r.mention for r in verified_roles]) or "None",
            inline=False,
        )
        embed.add_field(
            name="Active CAPTCHA policy",
            value=(
                f"length={policy['code_length']}, attempts={policy['attempts']}, "
                f"timeout={policy['timeout']}s, on_exhaust={policy['attempts_action']}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Account age policy",
            value=(
                f"min_hours={policy['min_age_hours']}, action={policy['age_action']} "
                "(applies to new members)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Pending expiry",
            value=(
                f"max={conf['pending_max_seconds']}s, "
                f"action={conf['pending_expiry_action']}, "
                f"timeout={conf['pending_timeout_minutes']}m"
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.group(name="verifymod")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_guild=True)
    async def verifymod(self, ctx: commands.Context) -> None:
        """Moderator verification management commands."""

    @verifymod.command(name="approve")
    async def verifymod_approve(self, ctx: commands.Context, member: discord.Member) -> None:
        conf = await self.config.guild(ctx.guild).all()
        await self._mark_verified(member, conf, reason=f"Manual verification approve by {ctx.author}")
        await self._record_case(member, "verification_success", f"Manually approved by {ctx.author}.")
        await ctx.send(f"Approved {member.mention}.")

    @verifymod.command(name="reject")
    async def verifymod_reject(self, ctx: commands.Context, member: discord.Member, *, reason: str = "") -> None:
        conf = await self.config.guild(ctx.guild).all()
        await self._set_pending(member, conf, reason=f"Manual verification reject by {ctx.author}")
        await self._clear_captcha_state(member)
        await self._record_case(
            member,
            "verification_rejected",
            f"Manually rejected by {ctx.author}. {reason}".strip(),
        )
        await ctx.send(f"Rejected {member.mention} and reset to pending state.")

    @verifymod.command(name="reset")
    async def verifymod_reset(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._clear_captcha_state(member)
        await self.config.member(member).pending_expired_handled.set(False)
        await ctx.send(f"Reset active CAPTCHA state for {member.mention}.")

    @verifymod.command(name="bulk")
    async def verifymod_bulk(
        self,
        ctx: commands.Context,
        action: Literal["inspect", "reset", "kick"] = "inspect",
        confirm: bool = False,
    ) -> None:
        conf = await self.config.guild(ctx.guild).all()
        pending_ids = set(int(r) for r in conf["pending_role_ids"])

        candidates = []
        for member in ctx.guild.members:
            if member.bot:
                continue
            if pending_ids and any(role.id in pending_ids for role in member.roles):
                candidates.append(member)

        if action == "inspect":
            preview = ", ".join(m.mention for m in candidates[:20]) or "None"
            suffix = "" if len(candidates) <= 20 else f" (+{len(candidates) - 20} more)"
            await ctx.send(f"Currently pending members: **{len(candidates)}**\n{preview}{suffix}")
            return

        if not confirm:
            await ctx.send("Destructive bulk actions require confirmation: set `confirm` to `true`.")
            return

        if action == "reset":
            for member in candidates:
                await self._clear_captcha_state(member)
                await self.config.member(member).pending_expired_handled.set(False)
            await ctx.send(f"Reset CAPTCHA state for **{len(candidates)}** pending members.")
            return

        if action == "kick":
            kicked = 0
            for member in candidates:
                if await self._kick_member(member, f"Bulk verification cleanup by {ctx.author}"):
                    kicked += 1
            await ctx.send(f"Kicked **{kicked}/{len(candidates)}** pending members.")

