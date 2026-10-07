# 4cd-cogs

Custom Red-DiscordBot cog collection.

## Included Cog: `verification`

A standalone CAPTCHA verification cog for Red 3.x that does **not** depend on custom ModLog cogs.
It uses Red's built-in `redbot.core.modlog` APIs opportunistically and continues working if modlog
casing is unavailable.

---
## Disclaimer

This project is partly vibecoded and provided as best-effort software with no guarantees.  
Expect edge-case slop, test in your own environment, and use at your own risk.

---

### Features

- Assigns configured pending/unverified roles to new non-bot members on join.
- Optionally DMs new pending members on join with verification onboarding instructions (enabled by default).
- Persistent **Verify** button panel in a configured verification channel.
- DM image CAPTCHA generated with Pillow + modal code entry.
- On success, adds configured access roles and removes pending roles.
- Config + temporary member verification state stored in Red `Config`.
- Persistent views re-registered on cog load/reload.
- CAPTCHA timeout cleanup, stale challenge detection, attempts limit, and optional kick on exhausted attempts.
- Account-age protection (safe default: disabled) with configurable action (`none`, `require`, `reject`).
- Raid mode toggles stricter override settings (attempts/timeout/length/account-age/action).
- Pending-member expiry policy separate from CAPTCHA expiry (`none`, `kick`, `timeout`).
- Moderator commands for approve/reject/reset and bulk inspect/manage with confirmation for destructive actions.
- Data deletion support via `red_delete_data_for_user`.

---

## Install

1. Add this repository path in Red:
   - `[p]repo add 4cd-cogs https://github.com/4liceD/4cd-cogs`
2. Install the cog
   - `[p]cog install 4cd-cogs verification`
3. Install dependencies if prompted (`pillow` is required).
4. Load the cog:
   - `[p]load verification`

## Required intents and permissions

### Bot intents
- **Server Members Intent** must be enabled:
  - Discord Developer Portal (privileged intent)
  - Red bot config/runtime

### Bot permissions
- Manage Roles
- Send Messages
- Embed Links
- Attach Files
- Use Application Commands
- Read Message History
- Optional for policies/actions:
  - Kick Members (attempt-exhausted or pending-expiry kick actions)
  - Moderate Members (pending-expiry timeout action)

### Role hierarchy
The bot's highest role must be above all pending/verified roles it needs to manage.

---

## Minimal setup

1. Create roles, for example:
   - `Unverified`
   - `Verified`
2. Set a verification channel:
   - `[p]verifyset channel #verification`
3. Configure roles:
   - `[p]verifyset pendingrole add @Unverified`
   - `[p]verifyset verifiedrole add @Verified`
4. Post the persistent panel:
   - `[p]verifyset panel`
5. Enable verification:
   - `[p]verifyset toggle true`

View current configuration:
- `[p]verifyset settings`

---

## Configuration commands

### Core
- `[p]verifyset toggle <true|false>`
- `[p]verifyset channel <#channel>`
- `[p]verifyset joindm <true|false>`
- `[p]verifyset panel`

### Pending roles
- `[p]verifyset pendingrole add <@role>`
- `[p]verifyset pendingrole remove <@role>`

### Verified roles
- `[p]verifyset verifiedrole add <@role>`
- `[p]verifyset verifiedrole remove <@role>`

### CAPTCHA policy
- `[p]verifyset captcha length <4-10>`
- `[p]verifyset captcha attempts <1-10>`
- `[p]verifyset captcha timeout <60-3600>`
- `[p]verifyset captcha onexhaust <none|kick>`
- `[p]verifyset test` (admin preview of a generated CAPTCHA image/code; does not alter member state)

### Account age
- `[p]verifyset accountage set <minimum_hours> <none|require|reject>`

Behavior:
- `none`: no account-age enforcement (default).
- `require`: members below threshold stay in pending verification flow.
- `reject`: members below threshold can be auto-rejected (kick if bot has permission).

### Raid mode
- `[p]verifyset raid enable <true|false>`
- `[p]verifyset raid attempts [value]` (omit value to clear override)
- `[p]verifyset raid timeout [value]` (omit value to clear override)
- `[p]verifyset raid length [value]` (omit value to clear override)
- `[p]verifyset raid accountage [minimum_hours] [require|reject]` (omit minimum_hours to clear override)
- `[p]verifyset raid onexhaust <default|none|kick>`

When raid mode is enabled, configured raid overrides replace normal verification settings for new joiners.

### Pending-member expiry policy
- `[p]verifyset pendingexpiry set <max_seconds> [none|kick|timeout] [timeout_minutes]`

Notes:
- This policy is separate from per-challenge CAPTCHA expiry.
- CAPTCHA expiry only invalidates challenge attempts; it does not itself kick users.

---

## Moderator commands

- `[p]verifymod approve <@member>`
- `[p]verifymod reject <@member> [reason]`
- `[p]verifymod reset <@member>`
- `[p]verifymod bulk [inspect|reset|kick] [confirm]`

Bulk safety:
- Destructive bulk actions require explicit `confirm true`.

---

## Recommended channel permission model

- Verification channel:
  - Pending role: can view/interact with verification panel
  - Verified role: optional deny view/send
- Main server channels:
  - Pending role: deny access
  - Verified role: allow access

Join onboarding behavior:
- On join, non-bot members in enabled guilds are set pending and can receive a DM that tells them to go to the configured verification channel and press **Verify**.
- The verification channel is intentionally kept uncluttered (no per-member join notifications are posted there).
- When users press **Verify**, the CAPTCHA image is sent in DM and the existing modal submission flow remains in-server via interaction.

---

## Troubleshooting

- **No join role assignment**: check Members intent and `verifyset toggle true`.
- **No join onboarding DM / CAPTCHA DM**: enable "Direct Messages" from server members in Discord privacy settings, then press **Verify** again.
- **Roles not applied**: check role hierarchy and `Manage Roles` permission.
- **Users cannot start verification**: verify configured channel and panel exists.
- **No modlog cases**: verification still works; modlog registration can be unavailable and is optional.

---

## Uninstall / data behavior

- Unload: `[p]unload verification`
- Remove package as normal for your environment.
- The cog implements `red_delete_data_for_user` for per-user member-state cleanup requests.

---

## Disclaimer

This project is partly vibecoded and provided as best-effort software with no guarantees.  
Expect edge-case slop, test in your own environment, and use at your own risk.
