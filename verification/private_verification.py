from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import discord

from .captcha import render_captcha
from .verification import Verification as BaseVerification
from .views import CaptchaPrompt, VerificationPanel


class Verification(BaseVerification):
    """Verification implementation with private in-channel CAPTCHA prompts."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.panel_view = VerificationPanel(self)

    async def handle_panel_click(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
            )
            return

        conf = await self.config.guild(interaction.guild).all()
        if not conf["enabled"]:
            await interaction.response.send_message(
                "Verification is currently disabled.", ephemeral=True
            )
            return

        configured_channel_id = conf["verification_channel_id"]
        if configured_channel_id and interaction.channel_id != int(configured_channel_id):
            channel = interaction.guild.get_channel(int(configured_channel_id))
            channel_name = channel.mention if channel else "the verification channel"
            await interaction.response.send_message(
                f"Please use {channel_name} to start verification.", ephemeral=True
            )
            return

        if not await self._is_unverified(interaction.user, conf):
            await interaction.response.send_message(
                "You are already verified.", ephemeral=True
            )
            return

        policy = self._active_policy(conf)
        if (
            policy["min_age_hours"] > 0
            and not self._account_age_ok(interaction.user, policy["min_age_hours"])
            and policy["age_action"] == "reject"
        ):
            await interaction.response.send_message(
                "Your account does not meet this server's minimum account age requirement.",
                ephemeral=True,
            )
            return

        async with self._lock:
            member_conf = self.config.member(interaction.user)
            state = await member_conf.all()
            now = int(datetime.now(UTC).timestamp())
            expires_at = int(state["captcha_expires_at"] or 0)
            active_code = (
                state["captcha_code"]
                if state["captcha_code"] and expires_at > now
                else None
            )

            if active_code is None:
                from .captcha import generate_code

                active_code = generate_code(policy["code_length"])
                await member_conf.captcha_attempts.set(0)

            expiry = now + int(policy["timeout"])
            await member_conf.captcha_code.set(active_code)
            await member_conf.captcha_expires_at.set(expiry)

        prompt = CaptchaPrompt(
            self,
            owner_id=interaction.user.id,
            timeout=policy["timeout"],
        )
        embed = discord.Embed(
            title="Your CAPTCHA",
            description=(
                "Enter the characters shown in the image below.\n"
                f"This challenge expires <t:{expiry}:R>."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url="attachment://verification_captcha.png")
        embed.set_footer(text="Only you can see this message.")

        await interaction.response.send_message(
            embed=embed,
            file=discord.File(
                render_captcha(active_code), filename="verification_captcha.png"
            ),
            view=prompt,
            ephemeral=True,
        )

        try:
            prompt.message = await interaction.original_response()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            prompt.message = None

    async def handle_modal_submit(
        self,
        interaction: discord.Interaction,
        value: str,
        *,
        prompt: CaptchaPrompt | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This can only be used in a server.", ephemeral=True
            )
            return

        conf = await self.config.guild(interaction.guild).all()
        policy = self._active_policy(conf)
        member_conf = self.config.member(interaction.user)
        state = await member_conf.all()
        now = int(datetime.now(UTC).timestamp())
        code = state["captcha_code"]
        expires_at = int(state["captcha_expires_at"] or 0)

        if not code or expires_at <= now:
            await self._clear_captcha_state(interaction.user)
            if prompt is not None:
                await prompt.close()
            await interaction.response.send_message(
                "That verification challenge is stale or expired. Press Verify again.",
                ephemeral=True,
            )
            return

        if "".join(value.upper().split()) == str(code).upper():
            await self._mark_verified(
                interaction.user,
                conf,
                reason="Verification: CAPTCHA solved",
            )
            await self._record_case(
                interaction.user,
                "verification_success",
                "Solved CAPTCHA successfully.",
            )
            if prompt is not None:
                await prompt.close()
            await interaction.response.send_message(
                "✅ Verification complete. You now have access.", ephemeral=True
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

        if str(policy["attempts_action"]).lower() == "kick":
            kicked = await self._kick_member(
                interaction.user, "Verification failed: attempts exhausted"
            )
            if kicked:
                if prompt is not None:
                    await prompt.close()
                await interaction.response.send_message(
                    "Verification failed and you have been removed from the server.",
                    ephemeral=True,
                )
                return

        if prompt is not None:
            await prompt.close()
        await interaction.response.send_message(
            "Verification failed. Press Verify again to request a new challenge.",
            ephemeral=True,
        )
