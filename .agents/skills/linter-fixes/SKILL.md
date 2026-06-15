---
name: linter-fixes
description: Fix Python linter violations (ruff, flake8/wemake, mypy). Use when lint checks fail and you need to resolve the reported errors.
argument-hint: "[file-path...]"
---

# Linter Fixes

## Goal

Resolve all linter violations reported by `just lint` while keeping changes minimal and preserving existing behavior.

## Workflow

1. Run `just lint` and capture the full output.
2. Group violations by file and error code.
3. For each violation, consult `references/error-codes.md` for the recommended fix.
4. Apply the smallest possible change that eliminates the violation.
5. Re-run `just lint` to confirm zero violations remain.
6. If a fix requires a `pyproject.toml` per-file-ignores entry, stop and request human approval (per AGENTS.md §I).

## Rules

- Never add `# ruff: noqa` (file-level ignores are forbidden).
- `# noqa` is allowed only when no simple refactor exists; always include a short reason.
- Do not suppress warnings in `pyproject.toml` without explicit human confirmation and an inline comment.
- Do not introduce new lint violations while fixing existing ones.
- Prompt literals and inline SQL may use `# noqa: E501 - <reason>` when splitting the line would hurt readability.

## Reference Files

| File | Content |
|------|---------|
| `references/error-codes.md` | Error code lookup tables for ruff, flake8-bandit, WPS, mypy, plus before/after examples |

Load `references/error-codes.md` when you need the recommended fix for a specific error code.
