from __future__ import annotations

import discord

from redbot.core.i18n import Translator

if False:  # pragma: no cover
    from .verification import Verification

_ = Translator("Verification", __file__)


class CaptchaModal(discord.ui.Modal, title="Server verification"):
    captcha_code = discord.ui.TextInput(
        label="Enter the CAPTCHA code",
        style=discord.TextStyle.short,
        min_length=4,
        max_length=32,
        required=True,
    )

    def __init__(self, cog: "Verification") -> None:
        super().__init__(custom_id="verification:captcha_modal")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_modal_submit(interaction, str(self.captcha_code.value))


class VerificationPanel(discord.ui.View):
    def __init__(self, cog: "Verification") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        custom_id="verification:start",
        emoji="✅",
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.cog.handle_panel_click(interaction)
