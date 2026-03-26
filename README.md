<table>
  <thead>
      <tr>
          <th style="text-align:center">English</th>
          <th style="text-align:center"><a href="./README_ko.md">한국어</a></th>
      </tr>
    </thead>
</table>

# 🦀ClaudeClaw — An OpenClaw-style personal AI assistant powered by Claude Code.

A persistent AI agent system built with `claude-agent-sdk`. Operates based on Claude Code's `settings.json`.  
This project is inspired by [OpenClaw](https://github.com/openclaw/openclaw).  
Runs as a Unix socket server, accepting messages from the CLI and REST API and proxying them to Claude.

---

## Features

| Feature                                | Command / Endpoint                                                   |
| -------------------------------------- | -------------------------------------------------------------------- |
| Daemon start / stop / restart / status | `claudeclaw start/stop/restart/status`                               |
| Send message (streaming)               | `claudeclaw -m "message"`                                            |
| stdin / pipe input                     | `echo "question" \| claudeclaw`                                      |
| View logs                              | `claudeclaw logs [--tail N]`                                         |
| Session management                     | `claudeclaw sessions`                                                |
| Cron job management                    | `claudeclaw cron add/list/delete/run/edit`                           |
| HTTP REST API                          | `POST /message`, `POST /message/stream`, `GET /status`, etc.         |
| Cron REST API                          | `GET /cron`, `POST /cron`, `PATCH /cron/{id}`, `DELETE /cron/{id}`, etc. |
| Discord integration                    | Auto-connect on daemon start (configure via `claudeclaw config set`) |
| Slack integration                      | Auto-connect on daemon start (configure via `claudeclaw config set`) |
| Heartbeat                              | Periodic polling via `claudeclaw config set heartbeat.every 30m`     |

---

## Setup

### Prerequisites

- Linux / Windows (WSL2)
- Python >= 3.14
- [An environment where claude-agent-sdk is available](https://platform.claude.com/docs/en/agent-sdk/overview)

### Dependencies

| Package                    | Purpose                   |
| -------------------------- | ------------------------- |
| `claude-agent-sdk>=0.1.48` | Claude AI Agent SDK       |
| `fastapi>=0.115.0`         | REST API framework        |
| `uvicorn>=0.30.0`          | ASGI server               |
| `apscheduler>=3.10,<4`     | Cron job scheduler (v3.x) |
| `discord.py>=2.3`          | Discord Bot (optional)    |
| `slack-bolt>=1.18`         | Slack Bot (optional)      |

### Installation

```bash
git clone <repository-url> ~/.claudeclaw
cd ~/.claudeclaw
pip install -r requirements.txt

# Add to PATH (~/.bashrc)
echo '[ -d "$HOME/.claudeclaw" ] && export PATH="$HOME/.claudeclaw:$PATH"' >> ~/.bashrc

# Enable tab completion (~/.bashrc)
echo 'eval "$(register-python-argcomplete claudeclaw)"' >> ~/.bashrc

source ~/.bashrc
```

> **Note:** The project must be placed in `~/.claudeclaw/`.
> Since `src/config.py` uses `Path.home() / ".claudeclaw"` as the base path, it will not work in a different directory.

---

## Usage

### Daemon Management

```bash
# Start (default port: 28789)
claudeclaw start

# Start with a specific port
claudeclaw start --port 18789

# Stop
claudeclaw stop

# Restart
claudeclaw restart

# Check status
claudeclaw status

# View logs
claudeclaw logs           # full output
claudeclaw logs --tail 50 # last 50 lines
```

### Sending Messages

```bash
# Simple send
claudeclaw -m "prompt"

# Specify a session
claudeclaw --session-id work -m "prompt"

# stdin / pipe
echo "question" | claudeclaw
cat report.txt | claudeclaw -m "Summarize this"
git diff | claudeclaw -m "Review this diff"
```

### Session Management

```bash
# List sessions
claudeclaw sessions

# Delete all sessions
claudeclaw sessions cleanup

# Delete a specific session
claudeclaw sessions delete <session-id>
```

### Cron Jobs

```bash
# Add a job (runs every morning at 9:00)
claudeclaw cron add "0 9 * * *" --name "morning" --session main -m "Organize today's tasks"

# List jobs
claudeclaw cron list

# Run manually
claudeclaw cron run <job-id>

# Edit a job (patch any combination of fields)
claudeclaw cron edit <job-id> --name "new name"
claudeclaw cron edit <job-id> --schedule "0 10 * * *" --message "Updated prompt"
claudeclaw cron edit <job-id> --session work
claudeclaw cron edit <job-id> --disable
claudeclaw cron edit <job-id> --enable

# Delete a job
claudeclaw cron delete <job-id>
```

### Heartbeat

Run periodic agent turns on the main session to process a checklist in `~/.claudeclaw/HEARTBEAT.md`.
Unlike Cron, Heartbeat preserves the main session's conversation context across executions.
If the agent replies with only `HEARTBEAT_OK`, the response is suppressed (logged only).

**Setup:**

```bash
# Enable heartbeat (every 30 minutes)
claudeclaw config set heartbeat.every 30m

# Disable heartbeat
claudeclaw config set heartbeat.every 0m

# Temporarily pause without losing the interval setting
claudeclaw config set heartbeat.disabled true

# Set active hours (only runs between 09:00 and 22:00)
claudeclaw config set heartbeat.active_hours.start "09:00"
claudeclaw config set heartbeat.active_hours.end "22:00"

# Restart daemon to apply
claudeclaw restart
```

**HEARTBEAT.md:**

Create `~/.claudeclaw/HEARTBEAT.md` with a checklist for the agent to process:

```markdown
# Heartbeat Checklist

- Check for any urgent pending tasks
- If nothing needs attention, reply HEARTBEAT_OK
```

> **Note:** If `HEARTBEAT.md` does not exist, the daemon runs with the default prompt only.
> If the file contains only headings or blank lines, the execution is skipped to reduce API calls.

**Verify:**

Check that the following messages appear in the logs:

```
Heartbeat scheduler started (interval=1800s)
HEARTBEAT_OK (suppressed)
```

If the agent has something to report, the log shows `Heartbeat alert (len=N)` instead.

### Discord Integration

Connect a Discord Bot to receive and reply to messages in a specified channel.

**Prerequisites:**

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications) and obtain a Bot Token
2. Enable **Message Content Intent** in the Bot settings
3. Invite the Bot to your server via the OAuth2 URL

**Setup:**

```bash
# Set Bot Token (required)
claudeclaw config set discord.bot_token <YOUR_BOT_TOKEN>

# Set target channel ID (required — right-click channel → Copy Channel ID)
claudeclaw config set discord.channel_id <YOUR_CHANNEL_ID>

# Set session to use (optional, default: "discord")
claudeclaw config set discord.session_id discord2

# Restart daemon to apply
claudeclaw restart
```

> **Note:** If `discord.channel_id` is not set, the Bot will not start (a warning is logged).
> Alternatively, set the token via the environment variable `DISCORD_BOT_TOKEN`.

**Verify:**

Check that the following messages appear in the logs:

```
Discord bot starting (channel_id=..., session=...)
Discord bot ready (logged in as <BotName>)
```

Once running, any message sent to the configured channel will be forwarded to Claude and replied to by the Bot.

### Slack Integration

Connect a Slack Bot to receive and reply to DMs and channel mentions via Socket Mode.

**Prerequisites:**

1. Create an application in the [Slack API Portal](https://api.slack.com/apps) and install it to your workspace
2. Enable **Socket Mode** and obtain an App-Level Token (`xapp-` prefix) with the `connections:write` scope
3. Add the following Bot Token Scopes: `chat:write`, `reactions:write`, `channels:history`, `im:history`, `app_mentions:read`
4. Enable the **Event Subscriptions** and subscribe to `message.im` and `app_mention` bot events
5. Obtain the Bot Token (`xoxb-` prefix) from the **Install App** page

**Setup:**

```bash
# Set Bot Token (required, xoxb- prefix)
claudeclaw config set slack.bot_token <YOUR_BOT_TOKEN>

# Set App Token (required, xapp- prefix)
claudeclaw config set slack.app_token <YOUR_APP_TOKEN>

# Set session to use (optional, default: "slack")
claudeclaw config set slack.session_id slack2

# Restart daemon to apply
claudeclaw restart
```

> **Note:** Both `slack.bot_token` and `slack.app_token` must be set for the Bot to start.
> Tokens can also be set via environment variables `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`.

**Advanced options:**

```bash
# Restrict DMs to specific users (default: "open" — allow all)
claudeclaw config set slack.dm_policy allowlist
claudeclaw config set slack.allow_from '["U01234567", "U09876543"]'

# Restrict channel mentions to specific channels (default: "open" — allow all)
claudeclaw config set slack.channel_policy allowlist
claudeclaw config set slack.channels '["C01234567"]'
```

**Verify:**

Check that the following messages appear in the logs:

```
Slack bot starting (session=...)
Slack bot ready (logged in as <BotName>, team=<TeamName>)
```

Once running, DMs to the Bot and `@mentions` in channels will be forwarded to Claude and replied to by the Bot.

### systemd Integration (if configured)

```bash
systemctl --user start claudeclaw
systemctl --user stop claudeclaw
systemctl --user status claudeclaw
```

---

## REST API

After starting the daemon, it is accessible at `http://localhost:28789` by default.

| Method   | Path              | Description                  |
| -------- | ----------------- | ---------------------------- |
| `POST`   | `/message`        | Send message (full response) |
| `POST`   | `/message/stream` | Send message (SSE streaming) |
| `GET`    | `/status`         | Daemon status and PID        |
| `GET`    | `/sessions`       | List sessions                |
| `DELETE` | `/sessions`       | Delete all sessions          |
| `DELETE` | `/sessions/{id}`  | Delete a specific session    |
| `GET`    | `/cron`           | List cron jobs               |
| `POST`   | `/cron`           | Add a cron job               |
| `PATCH`  | `/cron/{id}`      | Edit a cron job              |
| `DELETE` | `/cron/{id}`      | Delete a cron job            |
| `POST`   | `/cron/{id}/run`  | Run a cron job manually      |

> [!WARNING]
> This repository is under development. The source code and documentation are incomplete.
