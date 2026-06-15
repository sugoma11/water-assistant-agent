---
name: format-markdown
description: Format a markdown file by running "rumdl fmt". Use when a markdown file needs formatting, after editing specs, plans, or documentation.
argument-hint: "[file-path]"
allowed-tools: Bash(rumdl *), Read
---

## Goal

Format a markdown file using rumdl.

## Steps

1. Determine the markdown file path from the user request. If none is provided, ask for it.
2. Run `rumdl fmt <path>`.
3. Report completion and any errors.

## Rules

- Use `rumdl fmt` only; do not reformat via other tools.
- Only operate on files explicitly requested by the user.
- If the path is missing or ambiguous, ask for clarification.
