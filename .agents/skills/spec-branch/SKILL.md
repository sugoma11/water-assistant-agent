---
name: spec-branch
description: Create or update a feature branch for the current spec using repo conventions. Use before creating specs or plans tied to a new feature.
allowed-tools: Bash(git *), Read, Glob
---

## Goal

Create a feature branch for the active spec work while following repository naming rules.

## Steps

1. Fetch remote branches to ensure local metadata is current:

   ```bash
   git fetch --all --prune
   ```

2. Determine any required identifier (e.g., issue/Linear ID) from current branches or spec directories.
3. Generate a short, kebab-case title (2-4 words) from the spec focus.
4. Create or switch to the branch using the repository naming scheme.
5. Confirm the branch is active with `git branch --show-current`.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Keep titles concise and descriptive.
- Preserve technical acronyms (OAuth2, JWT, API).
- Do not invent new identifiers unless your workflow requires it.
- Keep spec directory naming consistent with the branch when applicable.
