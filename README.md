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

## Obtaining an OpenAI Token

1. Visit <https://platform.openai.com/api-keys>
2. Create a new API key
3. Copy the token - you will need it for the
   configuration file

## Preparing the TOML configuration

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

## Bot Architecture

Teledigest uses **two separate Telegram clients**:

1. **Bot client** - handles incoming bot commands and posts digests
   to the target channel. Requires a correct `bot_token` to be provided.
1. **User client** - authenticated with `api_id` and `api_hash`, used
   to fetch posts from Telegram channels.

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

| Command     | Description |
|-------------|-------------|
| `/status` | Shows amount of messages parsed, LLM prompt symbol count |
| `/today`  | Immediately triggers daily digest generation |
| `/ping`   | Returns "pong" to confirm bot responsiveness |

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

If needed, ensure the data directory is writable:

```bash
chmod -R a+rwX data
```

## First run & authentication

On the first run, Telethon may prompt for a login code.
Session files will be created in `./data`.

To perform authentication only and exit:

```bash
teledigest --config teledigest.conf --auth
```

Docker:

```bash
docker compose run --rm teledigest --auth
```

Do not delete the `data/` directory unless you want to re-authenticate.

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
