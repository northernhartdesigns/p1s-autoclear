# Releasing P1S Auto-Clear

## Version Bump Process

1. Update version in `pyproject.toml` (e.g. `0.1.0` → `0.2.0`).
2. Add a new `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md` with the changes. Move items from `[Unreleased]` into the new section.
3. Commit with message: `Bump version to X.Y.Z`
4. (Optional) Create a git tag: `git tag vX.Y.Z`

## Semantic Versioning

- **MAJOR** (x.0.0): Breaking changes
- **MINOR** (0.x.0): New features, backward compatible
- **PATCH** (0.0.x): Bug fixes, backward compatible
