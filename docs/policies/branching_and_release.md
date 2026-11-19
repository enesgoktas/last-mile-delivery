# Branching and Release Policy

## Branch naming
- Feature work: `codex/<feature-name>`
- Hotfixes: `codex/hotfix-<ticket>`
- Release branches: `release/<milestone>`

## Merge policy
- Squash merge to release branches.
- Required checks: tests, lint, migration validation, security scan.

## Versioning
- Semantic versioning for deploy artifacts.
- Tag format: `vMAJOR.MINOR.PATCH`.
