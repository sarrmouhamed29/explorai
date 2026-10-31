# 0001. GitFlow as the branching model

- Status: Accepted
- Date: 2026-10-31

## Context

The repository has a single developer who is learning professional workflows and must be able to explain them in interviews. The project will grow through eleven sub-projects, and we want released states to be identifiable and the integration branch to stay green. CI is the quality gate (local hooks can be bypassed).

## Decision

Use GitFlow:

- `main` holds tagged releases only.
- `develop` is the integration branch.
- `feature/*` branches start from `develop` and are merged back into it.
- `release/x.y.z` branches start from `develop`, are used to stabilise and write the changelog, and are merged into `main` and back into `develop`.
- `hotfix/*` branches start from `main` and are merged into both `main` and `develop`.

All merges go through pull requests with green CI; no direct push to `main` or `develop`. Commits follow Conventional Commits. Release tags are created by hand on `main` after the release branch is merged, and the changelog is generated with git-cliff.

Branch protection (making the CI checks required) is configured on GitHub by the maintainer, separately from the repository files.

## Alternatives considered

- **Trunk-based development** (short-lived branches into `main`, release from `main`). Lighter, and what many teams do today. Not chosen because the author wants to practise GitFlow, which is still common in many organisations and asked about in interviews.
- **GitHub Flow** (feature branches into `main`, deploy from `main`). Same reason: simpler, but it would not exercise release and hotfix branches.
- **release-please or semantic-release** to automate versions, changelog and tags. They assume releases are cut from the main line and fit GitFlow release branches poorly, so releases are done by hand with git-cliff for the changelog.

## Consequences

- GitFlow is heavier than a one-developer project needs: two long-lived branches, release and hotfix ceremonies, and merges back into `develop`. This is a deliberate trade-off for learning, not a claim that it is the most efficient flow.
- Every change needs a PR even when there is no reviewer, which keeps CI as the gate.
- Releases are manual (tag by hand), which is acceptable at this scale and keeps each release explainable.
- If the overhead becomes a problem, switching to trunk-based development means superseding this ADR.
