---
name: implement
description: Implement a requested phase, task range, or user story by deriving scope from the current branch and tasks.md.
argument-hint: "[phase/task-range/story]"
---

## Goal

Implement the requested Phase, task IDs, or user story for the active feature using repository conventions.

## Steps

1. Read the current branch with `git branch --show-current`.
2. If the branch matches `username/identifier-title`, extract the `identifier` segment.
3. Locate the matching spec folder under `specs/` that starts with the identifier.
4. Read `specs/<identifier-*>/tasks.md` and identify phases, task IDs, and user stories.
5. Determine scope from the user request in this order:
   - If explicit task IDs or ranges are provided (e.g., T001 or T001-T003), select only those tasks.
   - Else if a user story is provided (e.g., US2 or User Story 1), select tasks for that story.
   - Else if a phase is provided (e.g., Phase 2), select tasks in that phase.
   - Else default to Phase 1.
6. Implement the selected tasks in order, using the file paths listed in tasks.md.
7. After completing each task, run `just lint` and `just test`.
8. Mark task as done in tasks.md.
9. Create exactly one commit per task with a Conventional Commit message.

## Rules

- Derive scope from tasks.md; do not invent additional work.
- Do not implement tasks outside the selected scope.
- Keep changes minimal and follow repo conventions (imports, tool contracts, settings).
- Exactly one commit per task; do not batch multiple tasks into one commit.
- Ensure to run lint and test before committing.
