from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from redbot.core.i18n import Translator

if TYPE_CHECKING:
    from .verification import Verification

_ = Translator("Verification", __file__)

MAX_VIEW_TIMEOUT = 900


class CaptchaPrompt(discord.ui.View):
    """Private CAPTCHA image with a button that opens the code modal."""

    def __init__(
        self,
        cog: "Verification",
        owner_id: int,
        timeout: int,
    ) -> None:
        super().__init__(timeout=min(timeout, MAX_VIEW_TIMEOUT))

        self.cog = cog
        self.owner_id = owner_id
        self.message: discord.Message | None = None

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id == self.owner_id:
            return True

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "This CAPTCHA belongs to another member.",
                ephemeral=True,
            )

        return False

    @discord.ui.button(
        label="Enter Code",
        style=discord.ButtonStyle.primary,
        emoji="⌨️",
    )
    async def enter_code(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.send_modal(
            CaptchaModal(self.cog, self)
        )

    async def close(self) -> None:
        """Remove the private prompt from the user's view."""
        self.stop()

        if self.message is None:
            return

        try:
            await self.message.edit(view=None)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    async def on_timeout(self) -> None:
        await self.close()


class CaptchaModal(discord.ui.Modal, title="Server verification"):
    captcha_code = discord.ui.TextInput(
        label="Enter the CAPTCHA code",
        placeholder="Use the CAPTCHA image in the previous private message",
        style=discord.TextStyle.short,
        min_length=4,
        max_length=32,
        required=True,
    )

    def __init__(
        self,
        cog: "Verification",
        prompt: CaptchaPrompt,
    ) -> None:
        super().__init__(custom_id="verification:captcha_modal")

        self.cog = cog
        self.prompt = prompt

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await self.cog.handle_modal_submit(
            interaction,
            str(self.captcha_code.value),
            prompt=self.prompt,
        )


class VerificationPanel(discord.ui.View):
    """Persistent public verification panel."""

    def __init__(self, cog: "Verification") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        custom_id="verification:start",
        emoji="✅",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        await self.cog.handle_panel_click(interaction)
