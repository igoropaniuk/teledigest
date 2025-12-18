# Teledigest

[![License](https://img.shields.io/badge/licence-MIT-green)](https://opensource.org/license/mit)
[![Build on push](https://github.com/igoropaniuk/teledigest/actions/workflows/ci.yml/badge.svg)](https://github.com/igoropaniuk/teledigest/actions/workflows/ci.yml/badge.svg)

Teledigest is a Telegram digest bot that fetches posts from
configured Telegram channels, summarizes them using OpenAI models, and
publishes digests to a target channel.

## Prerequisites

Before installing and running Teledigest, ensure the following tools are
installed on your system:

### Python

The bot requires at least **3.12** version of **Python**.
Check your Python version:

``` bash
python3 --version
```

Install examples:

- **macOS (Homebrew)**

  ``` bash
  brew install python@3.12
  ```

- **Ubuntu/Debian**

  ``` bash
  sudo add-apt-repository ppa:deadsnakes/ppa
  sudo apt-get update
  sudo apt-get install python3.12 python3.12-venv python3.12-dev
  ```

### Poetry

The bot uses **Poetry** for dependency management and packaging. It requires at
least version **2.0** of Poetry.

Install Poetry:

``` bash
curl -sSL https://install.python-poetry.org | python3 -
```

or:

``` bash
pip install poetry
```

Verify installation:

``` bash
poetry --version
```

## Fetching the project

``` bash
git clone https://github.com/igoropaniuk/teledigest.git
cd teledigest
```

## Obtaining a Telegram Bot Token

1. Open Telegram and start a chat with `@BotFather`
2. Run `/newbot` and follow the instructions
3. Copy the generated **bot token** - you will need it for the
   configuration file

## Obtaining Telegram Application Credentials

1. Go to <https://my.telegram.org>
2. Log in with your phone number
3. Open **API Development Tools**
4. Create an application
5. Save **api_id** and **api_hash**

These are required for the Telegram client that fetches channel
messages.

## Obtaining an OpenAI API Key

1. Visit <https://platform.openai.com/api-keys>
2. Create a new API key
3. Copy the api key - you will need it for the
   configuration file

## Preparing the configuration file

Before running the bot, create a configuration file,
e.g. `teledigest.conf`:

``` toml
[telegram]
api_id = 123456
api_hash = "your_api_hash"
bot_token = "123456:ABCDEF"

[bot]
channels = ["@news", "@events"]
summary_target = "@digest_channel"
summary_hour = 21
summary_minute = 0
allowed_users = "@admin,123456789"

[llm]
model = "gpt-5.1-mini"
api_key = "YOUR_OPENAI_API_KEY"

[storage.rag]
keywords = [
    "sanctions", "economy", "energy",
    "market", "budget",
]

[llm.prompts]
system = """
You are a Telegram digest bot. Produce concise, well-structured daily summaries.
"""

user = """
Summarize the following messages for {DAY}:

{MESSAGES}
"""
```

`DAY` and `MESSAGES` will be automatically replaced by the bot while building
the final prompt.

### Important

**The bot must be added as an administrator to the target channel** so
it can publish digests.

Telegram Bot API doesn't permit joining channels automatically, so Teledigest
starts a regular user session requiring two-factor authentication specifically for
scraping channels.
**This will require inputting the phone number and 2FA dynamic password**
during the first run of the Teledigest.

Please check [First run & authentication](#first-run--authentication) section for
more details

## Bot Architecture

Teledigest uses **two separate Telegram clients**:

1. **Bot client** - handles incoming bot commands and posts digests
   to the target channel. Requires a correct `bot_token` to be provided.
   Always starts automatically
1. **User client** - authenticated with `api_id` and `api_hash`, used
   to fetch posts from Telegram channels. An additional Telegram client
   instance was introduced to overcome the limitations of the Telegram
   Bot API, which doesn't allow bots to join channels.

This separation ensures correct access to the Telegram channels.

## Installing and running the project with Poetry

### Install dependencies

``` bash
poetry install
```

Install pre-commit hook for code sanity checks:

```bash
poetry run pre-commit install
```

### Run the bot

``` bash
poetry run teledigest --config teledigest.conf
```

### Bot Commands

| Command   | Description |
|-----------|-----------------------------------------------------------------|
| `/auth`   | Authorize the user client so it canto access and scrape channels|
| `/start`  | Alias for `/help`                                               |
| `/help`   | Lists all supported bot commands                                |
| `/status` | Shows parsed/relevant counts (last 24h), schedule, model, ...   |
| `/today`  | Immediately triggers digest generation for last 24 hours        |
| `/digest` | Alias for `/today`                                              |

### Sanity checks

Teledigest uses `ruff`, `black`, `isort`, and `mypy`.

Run all checks:

``` bash
poetry run ruff check .
poetry run black --check .
poetry run isort --check-only .
poetry run mypy
poetry run pytest
```

To auto‑format:

``` bash
poetry run ruff check . --fix
poetry run black  .
poetry run isort .
```

## Running with Docker

The bot can be run fully containerized using Docker.
Configuration and persistent data (Telegram sessions + SQLite database) are mounted
from the host.

Docker is recommended for long-running or production deployments.

### Requirements

- Docker 20+
- Docker Compose v2 (`docker compose`)

### Configuration

Create a config file on the host, for example `teledigest.conf`:

```toml
[telegram]
api_id = 123456
api_hash = "YOUR_API_HASH"
bot_token = "YOUR_BOT_TOKEN"
sessions_dir = "/data"

[storage]
db_path = "/data/messages_fts.db"

[logging]
level = "INFO"
```

Always use absolute paths (`/data`) inside the container for persistent files.

Create a directory for persistent data:

```bash
mkdir -p data
```

This directory stores:

- Telegram `.session` files
- SQLite database for scraped messages

### Option A: Docker Compose (recommended)

#### docker-compose.yml

```yaml
services:
  teledigest:
    build: .
    image: teledigest:latest
    command: ["--config", "/config/teledigest.conf"]
    volumes:
      - ./teledigest.conf:/config/teledigest.conf:ro
      - ./data:/data
    user: "${GID:-1000}:${UID:-1000}"
    restart: unless-stopped
    environment:
      TZ: ${TZ}
```

#### Start the bot

```bash
docker compose up --build
```

You can also provide timezone configuration before running docker compose:

```bash
export TZ=$(cat /etc/timezone)
docker compose up --build
```

Run in background:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

Stop:

```bash
docker compose down
```

### Option B: Plain Docker (no Compose)

Build the image:

```bash
docker build -t teledigest .
```

Run the container:

```bash
export TZ=$(cat /etc/timezone)
docker run -e TZ=$TZ --rm \
   --user "$(id -u):$(id -g)" \
   -v "$(pwd)/teledigest.conf:/config/teledigest.conf:ro" \
   -v "$(pwd)/data:/data" teledigest:latest
```

### Permissions model

The container runs using the same UID/GID as the host user.
This avoids permission issues with bind-mounted volumes and prevents errors
such as:

- Permission denied
- SQLite readonly database errors
First run & authentication
If needed, ensure the data directory is writable:

```bash
chmod -R a+rwX data
```

## First run & authentication

On first run, if the user session is missing:

- The bot starts normally
- Scraping is disabled
- `/status` explicitly shows that authorization is required

### Authorizing via Telegram bot (recommended)

Authorization can be performed interactively via bot chat dialog:

1. `/auth`
2. Send your phone number (`+123456789`)
3. Send the 2FA code you receive.

When you authorize the user client via the `/auth` command, the bot asks you to
type the Telegram login code with spaces between each digit, for example:

`1 2 3 4 5`

This is **not** a protocol requirement, but a practical workaround for Telegram's
security system.

Telegram tries to detect situations where a login code might have been leaked or
shared. If the code is **forwarded** or **shared** from your account and then used
to log in from another client, Telegram may treat that as suspicious and block
the login, even though the code itself is correct. In that case you may see a
message similar to:

> the code was entered correctly, but the login was not allowed because the code
> was previously shared from your account.

By asking you to **type the code manually with spaces**, the bot encourages a
pattern that is clearly different from simply forwarding or copy-pasting the
original message with the code. On the bot side, those spaces are removed
before the code is sent to Telegram, so Telegram still receives the exact code
it issued.

In short:

- You type: `1 2 3 4 5`
- The bot converts it to: `12345`
- This reduces the chance of Telegram treating the login as a suspicious
  "shared code" login and blocking it.

If authorization fails, repeat `/auth`.

### CLI authorization (`--auth`)

It's possible to perform authentication via CLI and then exit:

```bash
poetry run teledigest --config teledigest.conf --auth
```

Or do this inside docker container:

```bash
docker run -it --rm --user "$(id -u):$(id -g)" \
   -v "$(pwd)/teledigest.conf:/config/teledigest.conf:ro" \
   -v "$(pwd)/data:/data" teledigest:latest \
   --config /config/teledigest.conf --auth
```

Expect this output during initial session registration:

```bash
$ poetry run teledigest --config teledigest.conf --auth
[INFO] teledigest - Logging configured at INFO level
[INFO] teledigest - Using session paths: user=data/user.session, bot=data/bot.session
[INFO] teledigest - Starting user & bot clients...
[INFO] teledigest - Channels to scrape (user account): @channel1, @channel2
[INFO] telethon.network.mtprotosender - Connecting to 0.0.0.0/TcpFull...
[INFO] telethon.network.mtprotosender - Connection to 0.0.0.0/TcpFull complete!
Please enter your phone (or bot token): +48888888888
Please enter the code you received: 12345
Signed in successfully as User; remember to not break the ToS!
[INFO] teledigest - Auth-only mode: skipping channel joins and handler registration.
[INFO] telethon.network.mtprotosender - Disconnecting from 0.0.0.0/TcpFull...
[INFO] telethon.network.mtprotosender - Not disconnecting (already have no connection)
[INFO] telethon.network.mtprotosender - Disconnection from 0.0.0.0/TcpFull complete!
[INFO] teledigest - Authentication completed
```

Then you can restart the bot without `--auth` param and it will use existing
sessions files.

Do not delete the `data/` directory unless you want to re-authenticate.

### Why bot-based authorization is preferred over CLI auth (especially in Docker)

Even CLI auth mode (`teledigest --auth`) still exists and works fine for local
development on your machine, it is **not recommended** as the primary method
in Docker / containerized environments.

There are a few reasons for that:

1. **Docker often has no usable stdin**

   The boot CLI-style `--auth` expects to read the phone number,
   login code and (optional) 2FA password from `stdin` (your terminal). In a
   typical Docker setup you will run the container in detached mode, or under
   an orchestrator (Kubernetes, docker-compose, etc.) with **no interactive
   TTY attached**.

   In that situation there is nowhere for Telethon to read from, so the process
   either blocks waiting on stdin or fails with an error. Attaching manually to
   container stdin just to type a one-time code is awkward and fragile.

2. **Non-interactive / automated deployments**

   Containers are usually started by scripts or orchestration tools, not by a
   human at a terminal. An interactive login step in the startup path breaks
   this model and makes fully automated deployments impossible. The bot-based
   `/auth` flow lets you keep the container fully non-interactive: you authorize
   once via Telegram, and the session file is reused next time the container
   starts.

3. **Clear separation of concerns**

   With bot-based auth, the container just runs the bot and user clients using
   existing session files. All interactive steps (phone, code, password) happen
   in Telegram itself, where you already expect to handle sensitive login
   information. The container only sees the resulting session, not the raw
   codes.

Because of these constraints, the recommended approach is:

- use `teledigest --auth` only for **local, manual** login when you are
  actually sitting at a terminal; or when you are deliberately managing
  sessions outside Docker, and
- use the `/auth` bot command for **normal Docker / production** deployments,
  where stdin is not reliably available and the process must remain
  non-interactive.

## Contributing

We follow a **clean history** approach with **fast‑forward merges**.

1. Fork the repository first
2. Fetch your fork:

   ``` bash
   git clone https://github.com/<your-username>/teledigest.git -b main
   cd teledigest
   ```

3. Create a feature branch:

   ``` bash
   git checkout -b feature/my-change
   ```

4. Commit your changes and push:

   ``` bash
   git push -u origin feature/my-change
   ```

5. Open a Pull Request on GitHub.

### Commit Message Style

This project uses the **Conventional Commits** specification:
<https://www.conventionalcommits.org/en/v1.0.0/>

Example commit messages:

```bash
$ git log --oneline
0d6c6ed docs(readme): add comprehensive project README
bee85ca chore: fix type and style issues
da78832 chore(dev): add black, isort, mypy and ruff as dev dependencies
654ca70 feat(config): migrate bot configuration to toml
05f221c feat(db): use messages from the last 24 hours for digest generation
4971b97 refactor: reorganize project into dedicated modules
...
```

## License

This project is licensed under the **MIT License**.
