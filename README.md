# hermes-a2a

Profile-safe Agent-to-Agent communication for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This plugin lets one Hermes profile expose an A2A-compatible HTTP endpoint and call other configured A2A agents through Hermes tools. It is designed for multi-profile setups where every agent has its own profile directory, port, state, secrets, and gateway session.

## What this provides

- Local A2A HTTP server with `/.well-known/agent.json` discovery.
- Hermes tools: `a2a_list`, `a2a_discover`, and `a2a_call`.
- Profile-local state, audit logs, and conversation storage.
- Config-driven remote agent registry.
- Bearer-token auth with env-based outbound tokens.
- HMAC-signed webhook wake into an existing Hermes gateway session.
- Deterministic webhook wake by `task_id` instead of trusting raw webhook text.
- Idempotent profile-safe installer with dry-run and backups.
- Zero runtime dependencies beyond the Python standard library.

## Requirements

- Hermes Agent v2026.4.23+ with plugin support.
- A Hermes profile directory containing `config.yaml`.
- `curl` or `wget` for one-line install/update/uninstall.
- `PyYAML` available to the Python interpreter used by Hermes or `python3` for config updates.

## Install without cloning

The recommended entrypoint is one script. It auto-detects Hermes profiles; in an interactive terminal it asks which profile to target when more than one profile exists. In non-interactive mode it uses the only detected profile, or you can override the target with `--profile`, `--hermes-home`, or `HERMES_HOME`.

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- install --dry-run
```

Review the output. If it targets the right profile, run the same command without `--dry-run`.

```bash
A2A_PORT=8081 \
A2A_PUBLIC_URL=http://127.0.0.1:8081 \
A2A_AGENT_NAME=my_agent \
A2A_AGENT_DESCRIPTION='My Hermes profile' \
A2A_HOME_PLATFORM=discord \
A2A_HOME_CHAT_TYPE=group \
A2A_HOME_CHAT_ID=123456789012345678 \
A2A_HOME_USER_ID=123456789012345678 \
A2A_HOME_USER_NAME='Hermes User' \
A2A_REMOTE_NAME=other_agent \
A2A_REMOTE_URL=http://127.0.0.1:8082 \
A2A_REMOTE_DESCRIPTION='Other Hermes profile' \
A2A_REMOTE_TOKEN_ENV=A2A_AGENT_OTHER_TOKEN \
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- install --dry-run
```

Target a specific Hermes profile explicitly when needed:

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- install --profile coder --dry-run

curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- install --hermes-home /path/to/hermes/profile --dry-run
```

To pin a branch, tag, or commit:

```bash
HERMES_A2A_REF=v0.3.0 \
  curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- install --dry-run
```

## Update

Update re-runs the same idempotent installer against the requested repo/ref. It backs up existing files and does not restart Hermes.

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- update --dry-run
```

Remove `--dry-run` after reviewing the output.

## Uninstall

Uninstall only removes the plugin files under the selected profile's `plugins/a2a` directory. It does not remove A2A config, env secrets, logs, or conversations. That is intentional: cleanup of profile config and history should be explicit and reviewable.

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-a2a/main/scripts/a2a.sh \
  | bash -s -- uninstall --dry-run
```

Remove `--dry-run` after reviewing the output.

## Local checkout workflow

```bash
git clone https://github.com/tickernelz/hermes-a2a.git
cd hermes-a2a
./install.sh --dry-run
./install.sh --profile coder --dry-run
./install.sh --hermes-home /path/to/hermes/profile --dry-run
```

## Target selection

| Option / variable | Purpose |
| --- | --- |
| Auto-detect | Finds `~/.hermes/config.yaml` and `~/.hermes/profiles/*/config.yaml`. Prompts in an interactive terminal when multiple profiles exist. |
| `--profile NAME` | Targets `~/.hermes/profiles/NAME`, with `default` and `main` mapping to `~/.hermes`. |
| `--hermes-home PATH` | Targets an explicit Hermes profile path. |
| `HERMES_HOME` | Environment override for the target Hermes profile path. |

## Installer environment

Common optional variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `A2A_HOST` | `127.0.0.1` | Host for the profile-local A2A HTTP server. |
| `A2A_PORT` | `8081` | Port for the A2A HTTP server. Use a unique port per profile. |
| `A2A_PUBLIC_URL` | `http://$A2A_HOST:$A2A_PORT` | URL advertised in the agent card. |
| `A2A_AGENT_NAME` | `hermes-agent` | Local agent name. |
| `A2A_AGENT_DESCRIPTION` | `Hermes A2A profile` | Local agent description. |
| `A2A_REQUIRE_AUTH` | `true` | Require Bearer token for inbound A2A POST requests. |
| `A2A_HOME_PLATFORM` | empty | Platform whose toolsets should include `a2a`, for example `discord` or `telegram`. |
| `A2A_HOME_CHAT_TYPE` | `dm` | Source chat type used for webhook session routing. |
| `A2A_HOME_CHAT_ID` | empty | Source/delivery chat ID for webhook session routing. |
| `A2A_HOME_USER_ID` | chat ID | Source user ID for webhook session routing. |
| `A2A_HOME_USER_NAME` | `user` | Source user name for webhook session routing. |
| `A2A_REMOTE_NAME` | empty | Optional remote agent registry name. |
| `A2A_REMOTE_URL` | empty | Optional remote agent URL. |
| `A2A_REMOTE_DESCRIPTION` | empty | Optional remote agent description. |
| `A2A_REMOTE_TOKEN_ENV` | empty | Env var name that stores the remote agent Bearer token. |

Curl entrypoint variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERMES_A2A_REPO` | `tickernelz/hermes-a2a` | GitHub repo to download. |
| `HERMES_A2A_REF` | `main` | Branch, tag, or commit to download. |
| `HERMES_A2A_CACHE` | `$XDG_CACHE_HOME/hermes-a2a` or `~/.cache/hermes-a2a` | Archive cache directory. |

## What install changes

Only the selected Hermes profile directory is mutated:

- copies `plugin/` and `dashboard/` to `plugins/a2a`
- backs up existing plugin/config/env before writing
- appends missing `.env` keys without overwriting existing values
- enables `plugins.enabled: [a2a]`
- adds `a2a` to `platform_toolsets.<A2A_HOME_PLATFORM>` when provided
- adds `a2a` to `known_plugin_toolsets.<A2A_HOME_PLATFORM>` when provided
- enables webhook support
- creates or updates the `a2a_trigger` webhook route
- writes `a2a.server`, `a2a.security`, and optional `a2a.agents` entries

The installer never restarts Hermes or any gateway.

## Webhook routing

Inbound A2A requests are stored as tasks. The plugin then sends an HMAC-signed webhook to Hermes to wake the target profile. The webhook payload contains a `task_id`; the plugin resolves that ID from the profile-local task queue before injecting anything into the agent turn.

Raw webhook text is not treated as user content. If a requested `task_id` is missing, the plugin falls back to the oldest pending task for compatibility.

The webhook secret intentionally exists in two places:

- `.env`: `A2A_WEBHOOK_SECRET` for the plugin signer
- `config.yaml`: `a2a_trigger.secret` for Hermes webhook validation

Keep them identical. Remote agent Bearer tokens should use `auth_token_env` and stay in `.env`, not inline in `config.yaml`.

## Configure remote agents manually

Prefer config-driven targets over direct URLs:

```yaml
a2a:
  enabled: true
  server:
    host: 127.0.0.1
    port: 8081
    public_url: http://127.0.0.1:8081
    require_auth: true
  security:
    allow_unconfigured_urls: false
    redact_outbound: true
    max_message_chars: 50000
    max_response_chars: 100000
    rate_limit_per_minute: 20
  agents:
    - name: other_agent
      url: http://127.0.0.1:8082
      description: Another Hermes profile
      auth_token_env: A2A_AGENT_OTHER_TOKEN
      enabled: true
      tags: [local]
      trust_level: trusted
```

```env
A2A_AUTH_TOKEN=<local-inbound-token>
A2A_AGENT_OTHER_TOKEN=<remote-inbound-token>
A2A_WEBHOOK_SECRET=<shared-webhook-secret>
```

## Usage

After the target Hermes gateway is restarted, the agent becomes discoverable at:

```text
http://127.0.0.1:8081/.well-known/agent.json
```

From Hermes, use:

- `a2a_list` to list configured remote agents
- `a2a_discover` to fetch a configured agent card
- `a2a_call` to send a message to a configured agent

A raw A2A JSON-RPC call looks like this:

```bash
curl -X POST http://127.0.0.1:8081 \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/send",
    "params": {
      "id": "task-001",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "Hello"}]
      }
    }
  }'
```

If a remote agent returns `working`, poll with `tasks/get`:

```bash
curl -X POST http://127.0.0.1:8081 \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tasks/get","params":{"id":"task-001"}}'
```

## Safety model

| Layer | Behavior |
| --- | --- |
| Profile isolation | Paths resolve under the selected profile directory; no hardcoded `~/.hermes` state. |
| Inbound auth | Bearer token required when `A2A_REQUIRE_AUTH=true` / `a2a.server.require_auth=true`. |
| Outbound auth | Remote tokens are resolved through `auth_token_env`. |
| Registry | Configured agent names are preferred; direct URLs are blocked by default. |
| Input validation | JSON-RPC and A2A task payloads are validated before queueing. |
| Queue lifecycle | Tasks move through pending, processing, completed, or failed states. |
| Webhook wake | Signed wake request routes by `task_id`; raw webhook text is ignored as content. |
| Persistence | Writes are profile-local, atomic, and redacted where appropriate. |
| Rate limit | Inbound requests are rate-limited per remote address. |

## File layout

Installed under the selected profile's `plugins/a2a/`:

| File | Purpose |
| --- | --- |
| `__init__.py` | Plugin entrypoint, hook registration, slash command, server startup. |
| `server.py` | A2A JSON-RPC server, task queue, webhook trigger. |
| `tools.py` | Hermes tool implementations for outbound A2A. |
| `config.py` | Config loading, registry normalization, token env resolution. |
| `paths.py` | Profile-safe path resolution. |
| `security.py` | Filtering, redaction, rate limiting, audit helpers. |
| `persistence.py` | Profile-local conversation persistence. |
| `schemas.py` | Tool schemas. |
| `plugin.yaml` | Plugin manifest. |
| `dashboard/` | Dashboard assets and plugin API support. |

## Development

```bash
bash -n install.sh
bash -n uninstall.sh
bash -n scripts/a2a.sh
python -m py_compile plugin/*.py dashboard/plugin_api.py tests/*.py
python -m pytest -q
git diff --check
```

## Known limitations

- No A2A streaming/SSE support yet.
- Agent card skills are still static.
- Config and env cleanup after uninstall is manual.
- The final privacy boundary is still the agent's judgment; technical filters reduce leaks but cannot prove intent safety.

## License

MIT

## Continuity note

This fork continues work from the original [`iamagenius00/hermes-a2a`](https://github.com/iamagenius00/hermes-a2a) repository, with additional profile-safety, installer, security, and webhook-routing hardening for multi-profile Hermes deployments.
