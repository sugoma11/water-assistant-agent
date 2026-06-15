---
name: retrospective
description: Review all implemented changes on the branch for code quality and architectural decisions. Use after all implementation tasks are complete to surface optimizations.
allowed-tools: Read, Grep, Glob, Bash(git *)
---

## Goal

Look backward at the full set of implemented changes, assess code quality and architectural choices against the original plan, and produce actionable optimization tasks or confirm the implementation is clean.

## Prerequisites

- All implementation tasks in `tasks.md` MUST be marked done (except the retrospective task itself).
- The feature branch MUST have commits to compare against the base branch.
- `spec.md`, `plan.md`, and `tasks.md` MUST exist in the feature's `specs/<identifier>/` folder.

## Steps

1. **Identify the branch and spec folder**:
   - Read the current branch with `git branch --show-current`.
   - Extract the identifier and locate `specs/<identifier-*>/`.
   - Load `spec.md`, `plan.md`, and `tasks.md` for context.

2. **Collect the full branch diff**:
   - Determine the base branch (typically `main`) with `git merge-base HEAD main`.
   - Run `git diff <merge-base>..HEAD` to get the complete set of changes.
   - Run `git log --oneline <merge-base>..HEAD` for commit history context.

3. **Review code quality** — check each changed file for:
   - **DRY violations**: identify copy-pasted or near-identical logic across new/modified files. When common patterns emerge, recommend extracting shared helpers, base classes, or utility functions.
   - **SOLID compliance**: single responsibility per module/class, open-closed extensibility, dependency inversion where appropriate. Flag god-functions, tight coupling, and missing abstractions.
   - Naming clarity (modules, functions, variables follow repo conventions).
   - Error handling consistency (tool contract compliance, structured returns).
   - Typing completeness (strict mypy, no `object` annotations, proper use of generics).
   - Logging discipline (structlog with keyword args, `logger.exception` in `except` blocks).
   - Import discipline (absolute imports only, no `__init__.py` logic).
   - Test coverage for new code paths.

4. **Review architectural decisions** — evaluate against AGENTS.md and the plan:
   - Layer boundaries (agent → tools → functions separation).
   - Coupling between modules (are dependencies minimal and explicit?).
   - Performance considerations (unnecessary work, caching within tenant boundaries).
   - Settings usage (no direct env-var access outside settings loader).
   - Data engineering conventions (dbt refs, Trino timestamp format, schema strictness).
   - Whether the plan's phased approach led to redundant abstractions or unnecessary indirection.

5. **Produce `retrospective.md`** in the feature's `specs/<identifier>/` folder with:
   - **Summary**: one-paragraph overall assessment (clean / needs work).
   - **Code Quality Findings**: grouped by severity (🔴 HIGH, 🟡 MEDIUM, 🟢 LOW), each with:
     - A stable ID (R-001, R-002, …).
     - File path and line references.
     - Issue description, impact, and recommended fix.
   - **Architectural Observations**: higher-level notes on design choices that could be improved.
   - **Metrics**: files changed, new files, total lines added/removed, finding counts by severity.

6. **Update `tasks.md`**:
   - If findings exist (any 🔴 or 🟡 severity): append new tasks (continuing the existing ID sequence) for each actionable finding, grouped under a new phase titled `## Phase N: Retrospective Fixes`. Mark the retrospective review task itself as done.
   - If no actionable findings: mark the retrospective review task as done and append a note `<!-- Retrospective: no issues found -->` after it.

## Rules

- Spec artifacts live in `./specs/<identifier>/`.
- Do NOT modify source code — only produce `retrospective.md` and update `tasks.md`.
- Findings MUST reference concrete file paths and line numbers from the branch diff.
- Focus on actionable, high-signal issues — skip stylistic nitpicks already covered by linters.
- DRY and SOLID principles are mandatory review criteria. Copy-pasted logic MUST be flagged with a concrete recommendation to generalize (e.g., extract a shared function, introduce a base class, or parameterize the common pattern).
- Respect repository governance (AGENTS.md) when evaluating conventions.
- The retrospective review task in `tasks.md` MUST always be marked done after this skill runs.
- New tasks added to `tasks.md` MUST continue the existing ID sequence (e.g., if last task is T035, new tasks start at T036).
- Preserve all existing completed tasks in `tasks.md` — never remove history.

## Output

- `retrospective.md` in the feature's specs folder.
- Updated `tasks.md` with optimization tasks (or confirmation that the review passed).
