# 4cd-cogs

Custom Red-DiscordBot cog collection.

## Verification CAPTCHA flow

The verification panel remains public in the configured channel. When a member presses **Verify**, the CAPTCHA image is sent as a private ephemeral message visible only to that member. The private prompt includes **Enter Code**, which opens the modal. After successful verification, the prompt is cleaned up where Discord permits.

Join onboarding DMs remain instruction-only; CAPTCHA images are not sent by DM.
