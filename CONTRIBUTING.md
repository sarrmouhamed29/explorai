# Contributing

This project follows GitFlow, Conventional Commits and a written definition of done. The reasoning behind the main choices is in [`docs/adr/`](docs/adr/README.md).

## Branching: GitFlow

| Branch | Purpose | Created from | Merged into |
| --- | --- | --- | --- |
| `main` | Tagged releases only | | |
| `develop` | Integration of finished work | | |
| `feature/<name>` | One change or sub-project step | `develop` | `develop` |
| `release/x.y.z` | Stabilise and write the changelog for a release | `develop` | `main`, then back into `develop` |
| `hotfix/<name>` | Urgent fix of a released version | `main` | `main` and `develop` |

Rules:

- Every change reaches `develop` or `main` through a pull request. No direct pushes to `main` or `develop`.
- The CI checks (backend, frontend, contract, docker, security) must be green before merging.
- Branch protection that enforces the two rules above is configured on GitHub by the maintainer, separately from this repository's files. Until it is enabled, the rules are a convention.
- Pull requests use the template in `.github/pull_request_template.md`.
- Dependabot pull requests target `develop` (`target-branch` in `.github/dependabot.yml`). Dependabot reads its configuration from the default branch, so it only becomes active once this file reaches `main` (first release).

## Commits: Conventional Commits

Format: `type(optional scope): short description in the imperative`. A body may follow after a blank line.

Types used here: `feat`, `fix`, `docs`, `test`, `build`, `ci`, `chore`, `refactor`, `perf`, `style`.

Examples taken from this repository's history:

```
feat(backend): add liveness and readiness routes
build(infra): containerize the backend and add it to Compose
docs(backend): fix integration test instructions and document probe timeout
ci: add GitHub Actions workflows and dependency updates
```

A breaking change is marked with `!` after the type or scope (`feat(api)!: ...`) or a `BREAKING CHANGE:` footer.

Commits written with the help of Claude end with a blank line and a trailer such as:

```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
```

The commit message format is checked by a local hook and the changelog is generated from these messages, so keep them accurate.

## Local hooks

Hooks give fast feedback before a commit. They are a convenience: they can be skipped with `--no-verify`, so CI is the real gate (see [`docs/concepts/00-foundations.md`](docs/concepts/00-foundations.md)).

Install them once per clone:

```bash
uv sync
uv run pre-commit install --install-hooks
```

`.pre-commit-config.yaml` sets `default_install_hook_types: [pre-commit, commit-msg]`, so this single command installs both the pre-commit hooks (ruff, gitleaks, prettier, eslint) and the commit-msg hook (Conventional Commits check). `mypy` is not a hook (too slow per commit); CI runs it.

Requirements on your `PATH`:

- `bash` and `pnpm`: the prettier and eslint hooks are shell commands that call `pnpm exec`. Run `pnpm --dir frontend install` first.
- `gitleaks` v8.30.1. It is a local hook that runs the release binary, because the upstream hook builds from source with Go, which hung on the maintainer's machine. Install it from the official release page, <https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1>:
  1. Download the archive for your platform and `gitleaks_8.30.1_checksums.txt` from that page.
  2. Verify the archive against the checksums file (`sha256sum --check` on Linux and macOS, `Get-FileHash -Algorithm SHA256` on Windows, and compare by hand).
  3. Extract the `gitleaks` binary into a directory that is on your `PATH`.
  4. Check with `gitleaks version`.

Run all hooks on every file at any time with `uv run pre-commit run --all-files`.

## Definition of done

A change is done when all of the following hold:

1. Tests cover the new behaviour and pass (`pytest` once per Python package: `backend`, `db`, `pipelines`, each enforcing the 80% coverage gate; Vitest for the frontend).
2. Lint, format and type checks are green: `ruff check`, `ruff format --check` and `mypy` (strict) for Python (mypy is run once per package with `--config-file <package>/pyproject.toml`, otherwise it ignores the package's strict settings); ESLint, Prettier and `tsc` for the frontend.
3. If the API changed, `backend/openapi.json` and `frontend/src/api/schema.d.ts` are regenerated and committed (see below). CI fails on drift.
4. Documentation is updated in the same pull request: package READMEs, the root README, `docs/concepts/`, and a new ADR in `docs/adr/` for any significant technical choice.
5. Every new sub-project spec contains an "evaluation criteria" section (how we know it works) and a "threats and guardrails" section (what can go wrong and how we protect against it).
6. No secrets in the diff or the history. Only placeholders in `.env.example`.
7. CI is green on the pull request.

Regenerating the API contract and client after a backend route or schema change:

```bash
uv run --package explorai-backend python backend/scripts/export_openapi.py
pnpm --dir frontend gen:api
```

## Releases

1. Create `release/x.y.z` from `develop`.
2. Generate the changelog from the commit history with git-cliff, using the `cliff.toml` at the root. For example, with uv: `uvx git-cliff --tag vX.Y.Z -o CHANGELOG.md`.
3. Bump versions if needed, fix last-minute issues on the release branch only, open a PR to `main` and get CI green.
4. After merging into `main`, create the tag by hand on `main` (`git tag -a vX.Y.Z -m "vX.Y.Z"`, then push the tag), and merge `main` back into `develop`.

Release automation tools (release-please, semantic-release) were not chosen because they fit GitFlow release branches poorly; see [ADR 0001](docs/adr/0001-gitflow.md).

## Windows notes

- Use `127.0.0.1`, not `localhost`. On Windows `localhost` resolves to `::1` (IPv6) first. The services listen on IPv4 only, so each probe waits for a failed IPv6 attempt before falling back, and health checks stall (about 5 seconds for PostgreSQL's libpq, about 2 seconds for Redis).
- Line endings: `.gitattributes` forces LF for every text file (`* text=auto eol=lf`), whatever `core.autocrlf` says. CRLF in a shell script or Dockerfile breaks it inside a Linux container, CRLF in the generated files would make the CI drift check fail, and CRLF anywhere makes `pnpm format:check` fail.
- Running the backend natively on Windows needs the selector event loop for psycopg async; the tests set it in `backend/tests/conftest.py`. Linux containers do not need it.
- Use Git Bash for the shell commands in the documentation.
