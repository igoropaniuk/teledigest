# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-12-20

Initial public release of Teledigest — an LLM-driven framework for building
Telegram digest and channel-analysis bots.

### Added

- Initial implementation of the Telegram digest bot with Telethon-based user
  client and bot client
- TOML-based bot configuration (`teledigest.conf`)
- SQLite message store using `sqlite-utils`; digest is generated from messages
  received in the last 24 hours
- OpenAI-powered summarization pipeline
- Scheduler with support for hourly and minute-level (`summary_minute`) digest
  intervals
- Bot commands: `/start`, `/help`, `/digest` (alias `/today`), `/auth`
- Bot-based `/auth` flow for authenticating the user (Telethon) client without
  direct CLI access
- `--auth` CLI option for one-time interactive Telegram authentication
- Configurable Telegram session directory via `[telegram] session_dir`
- Prettier bot command output formatting
- Dockerfile and `docker-compose.yml` for containerised deployments
- Pre-commit hooks (black, isort, mypy, ruff)
- GitHub Actions CI workflow with markdown linter and isort checks
- pytest suites covering the TOML config parser and database layer

### Changed

- Migrated dependency management from `requirements.txt` to Poetry
- Reorganised source tree into dedicated modules
  (`config`, `db`, `llm`, `scheduler`, `bot`, `client`, `cli`)
- Renamed LLM config key `token` → `api_key` to match OpenAI SDK terminology

### Removed

- `/ping` bot command

### Fixed

- OpenAI client usage repaired after migration to the new SDK API (`>=2.x`)
- `bot_client` initialisation order — ensured it is ready before first use
- CLI no longer prints a full traceback on expected errors unless `--debug` is
  passed
- Python patch version included in CI venv cache key to prevent stale caches

[0.1.0]: https://github.com/igoropaniuk/teledigest/releases/tag/v0.1.0
