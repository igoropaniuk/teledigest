# Contributing to Teledigest

Thanks for your interest in contributing to **Teledigest**!
Teledigest is an LLM-driven framework for building Telegram digest and
channel-analysis bots.

This guide explains how to set up a development environment, run checks,
and submit changes.

---

## Development setup

### Prerequisites

- Python **3.12+**
- Poetry **2.0+**
- Git

### Clone the repository

```bash
git clone https://github.com/igoropaniuk/teledigest.git
cd teledigest
```

### Install dependencies

```bash
poetry install
```

### Install pre-commit hooks (recommended)

```bash
poetry run pre-commit install
```

---

## Running tests and checks

Teledigest uses `ruff`, `black`, `isort`, `mypy`, and `pytest`.

### Run all checks

```bash
poetry run ruff check .
poetry run black --check .
poetry run isort --check-only .
poetry run mypy
poetry run pytest
```

### Auto-format

```bash
poetry run ruff check . --fix
poetry run black .
poetry run isort .
```

---

## What to test

When adding or changing behavior, please include unit tests.

Guidelines:
- Prefer **pure / deterministic** functions and small units.
- Mock external services (Telegram network calls, OpenAI/LLM backends).
- Tests should not require network access.

---

## Pull request workflow

We follow a **clean history** approach with **fast-forward merges**.

1. Fork the repository
2. Clone your fork:

   ```bash
   git clone https://github.com/<your-username>/teledigest.git -b main
   cd teledigest
   ```

3. Create a feature branch:

   ```bash
   git checkout -b feature/my-change
   ```

4. Make changes + add tests
5. Run checks (a script that runs all tools at once):

   ```bash
   poetry run bash ./scripts/ci-check
   ```

6. Commit and push to your fork:

   ```bash
   git add .
   git commit -m "feat: your descriptive commit message"
   git push -u origin feature/my-change
   ```

7. Open a Pull Request on GitHub

### PR guidelines

- Keep PRs **focused** (avoid mixing refactors with unrelated functional changes).
- Ensure CI passes.
- Add a clear PR description explaining **what** and **why**.

---

## Commit message style

This project uses the **Conventional Commits** specification:
<https://www.conventionalcommits.org/en/v1.0.0/>

Format:

```text
<type>(optional-scope): short summary

optional body
```

Common types:
- `feat` - new feature
- `fix` - bug fix
- `docs` - documentation changes
- `test` - adding/updating tests
- `refactor` - internal refactoring
- `chore` - tooling/meta changes
- `ci` - CI-related changes (Github CI actions)

Examples:

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

### Signed-off-by

All commits must carry a `Signed-off-by` trailer.  It is your attestation
that you wrote the change and have the right to submit it under the project's
license (see the [Developer Certificate of Origin](https://developercertificate.org/)).

Add it automatically with the `-s` flag:

```bash
git commit -s -m "feat: your descriptive commit message"
```

This appends a line like the following to the commit body:

```text
Signed-off-by: Your Name <you@example.com>
```

Git reads your name and e-mail from `user.name` / `user.email` in your
git config, so make sure those are set correctly before you start.

### Squashing fix commits

To keep the commit history clean and easier to follow, please squash fix
commits into the original commits they relate to, instead of adding separate
"fix" commits.

Having standalone fix commits makes the history harder to read and review
later.  When each commit is logically complete (i.e. it compiles, passes
tests, and includes any follow-up fixes), it:

- Makes `git blame` more meaningful
- Keeps the history easier to understand
- Simplifies potential reverts
- Maintains a clean and intentional commit narrative

Use an interactive rebase to squash the fixes into the relevant commits:

```bash
git rebase -i <your-feature-branch>
```

---

## Questions / ideas

If you’re unsure about an implementation approach or want to propose a bigger
change, open an issue first so we can discuss direction before you invest time.
