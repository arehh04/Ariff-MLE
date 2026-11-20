# OneCredit MLE take-home: company recommendation system

A consumer reads company changes from RabbitMQ queue `company-events` into Postgres + pgvector, and a
FastAPI service returns similar companies with card details. Implementation has not started; this README
is finalised in prompt 11 from real runs.

Template reference: https://github.com/OneCreditMY/mle-take-home (assessment `2026.11`).

## Environment (from the template)
- Postgres+pgvector: `postgresql://postgres:postgres@localhost:5432/companies` (`POSTGRES_PORT` overrides)
- RabbitMQ AMQP `5672`, UI http://localhost:15672 (guest/guest) (`RABBITMQ_PORT`, `RABBITMQ_UI_PORT` override)
- Python 3.14 via uv; commands: `just setup`, `just up`, `just publish`, `just down` (`down` deletes data)

## Tooling without global installs
`uv` and `just` live in a small venv outside the repo (no sudo, nothing global), because the machine has neither:
```bash
python3 -m venv ~/.venvs/tools && ~/.venvs/tools/bin/pip install uv rust-just
export PATH="$HOME/.venvs/tools/bin:$PATH"     # per shell; or prefix each command
just setup && just up
```
### Install the dependencies (always inside a virtual env)
Never `pip install` into the system Python. The project needs Python 3.14:
```bash
# with uv (it fetches Python 3.14 for you; recommended)
uv venv --python 3.14 && uv pip install -r requirements.txt
source .venv/bin/activate

# or with your own python3.14
python3.14 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```
`requirements.txt` is pinned and generated from `uv.lock` (runtime and dev packages). `uv.lock` stays the source of truth:
after `uv add ...`, refresh it with `uv export --no-hashes --no-emit-project -o requirements.txt`.
`just setup` (`uv sync --locked`) does the same thing and is the usual way.

`uv` downloads Python 3.14 itself and creates the project's own `.venv` (gitignored). If you already have `uv` and
`just` installed normally, skip this.

## Layout
```
AGENTS.md        agent rules and guardrails (read first)
DESIGN.md        decisions, assumptions, results (fill from real runs)
docs/            00 assessment brief (summary of the template README), 01 requirements, 02 feed findings, 03 architecture, 04 API, 05 schema, 06 test plan, 07 design skeleton
prompts/         one prompt per working session, in order
mle/ scripts/ tests/unit tests/integration migrations/    to be written
```
Template files (`producer/`, `data/feed.jsonl`, `docker-compose.yml`, `Justfile`, `pyproject.toml`,
`ASSESSMENT_VERSION`, `CHANGELOG.md`) are provided by the assessment and must not be modified.

## Working order
Prompts `00`-`12` in `prompts/`, one per session. Hard stop at 6 hours; cut order in `docs/01-requirements.md`.
