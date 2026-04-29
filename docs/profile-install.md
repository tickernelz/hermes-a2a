# Profile-safe install workflow

This plugin is profile-local. Install it once per Hermes profile by setting `HERMES_HOME` explicitly. Do not install it into sibling profiles unless that profile should participate in A2A.

## Jono ↔ Yanto example

Jono/default:

```bash
cd /home/zhafron/Projects/hermes-a2a
HERMES_HOME=/home/zhafron/.hermes \
A2A_PORT=8081 \
A2A_PUBLIC_URL=http://127.0.0.1:8081 \
A2A_AGENT_NAME=jono \
A2A_AGENT_DESCRIPTION='Jono default Hermes profile' \
A2A_HOME_PLATFORM=discord \
A2A_HOME_CHAT_TYPE=group \
A2A_HOME_CHAT_ID=1499028849261023322 \
A2A_HOME_USER_ID=287600440659410944 \
A2A_HOME_USER_NAME=Zhafron \
A2A_REMOTE_NAME=yanto_coder \
A2A_REMOTE_URL=http://127.0.0.1:8082 \
A2A_REMOTE_DESCRIPTION='Yanto Coder Hermes profile' \
A2A_REMOTE_TOKEN_ENV=A2A_AGENT_YANTO_TOKEN \
./install.sh --dry-run
```

If the dry run looks right, remove `--dry-run`.

Yanto Coder:

```bash
cd /home/zhafron/Projects/hermes-a2a
HERMES_HOME=/home/zhafron/.hermes/profiles/hermes_yanto_coder \
A2A_PORT=8082 \
A2A_PUBLIC_URL=http://127.0.0.1:8082 \
A2A_AGENT_NAME=yanto_coder \
A2A_AGENT_DESCRIPTION='Yanto Coder Hermes profile' \
A2A_HOME_PLATFORM=discord \
A2A_HOME_CHAT_TYPE=group \
A2A_HOME_CHAT_ID=1499028849261023322 \
A2A_HOME_USER_ID=287600440659410944 \
A2A_HOME_USER_NAME=Zhafron \
A2A_REMOTE_NAME=jono \
A2A_REMOTE_URL=http://127.0.0.1:8081 \
A2A_REMOTE_DESCRIPTION='Jono default Hermes profile' \
A2A_REMOTE_TOKEN_ENV=A2A_AGENT_JONO_TOKEN \
./install.sh --dry-run
```

If the dry run looks right, remove `--dry-run`.

## What install.sh changes

For the selected `HERMES_HOME` only:

- copies `plugin/` and `dashboard/` to `$HERMES_HOME/plugins/a2a`
- creates timestamped backups of existing plugin/config/env before mutating
- enables `plugins.enabled: [a2a]` idempotently
- adds `a2a` to `platform_toolsets.<A2A_HOME_PLATFORM>` and `known_plugin_toolsets.<A2A_HOME_PLATFORM>` idempotently
- writes `webhook.extra.routes.a2a_trigger` and `platforms.webhook.extra.routes.a2a_trigger`
- writes profile-local `a2a.server`, `a2a.security`, and optional remote agent registry entry
- appends missing `.env` keys without overwriting existing values

It does not restart Hermes or any gateway.

## Webhook secret placement

`A2A_WEBHOOK_SECRET` is stored in `.env` because the plugin needs it to sign the internal wake request. The same random value is also written into `webhook.extra.routes.a2a_trigger.secret` and `platforms.webhook.extra.routes.a2a_trigger.secret` because Hermes webhook validation reads route secrets from `config.yaml`. Treat both copies as sensitive profile-local config and keep them identical. Remote agent tokens still belong only in `.env` via `auth_token_env`.

## Adding another agent later

Use the same command shape with a new profile path, unique port, and remote registry values. Prefer `auth_token_env` for all remote secrets. Keep remote agent bearer tokens in that profile's `.env`, not in `config.yaml`. The webhook wake secret is the intentional exception: Hermes validates it from the `a2a_trigger` route in `config.yaml`, while the plugin signs requests with `A2A_WEBHOOK_SECRET` from `.env`, so both values must match.

## Safety rules

- Always set `HERMES_HOME` explicitly; the installer refuses to run without it.
- Always run `./install.sh --dry-run` first.
- Review `$HERMES_HOME/config.yaml` and `$HERMES_HOME/.env` before restarting a gateway.
- Never install into a protected isolated profile unless you intentionally want that profile in A2A.
