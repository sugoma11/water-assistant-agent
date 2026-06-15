---
name: tasks
description: Generate an actionable, dependency-ordered task list from spec and plan artifacts. Use after planning to break work into executable tasks.
---

## Goal

Produce tasks that are specific, ordered, and independently executable.

## Steps

1. Load the spec and plan; include optional artifacts (data model, contracts, research, quickstart) if present.
2. Extract user stories and priorities, then map tasks to those stories.
3. Create setup and foundational tasks before story-specific work.
4. Use the repository task template if available; otherwise use a checklist format with stable IDs.
5. Mark parallelizable tasks explicitly and capture dependencies between phases.
6. Add tests only when requested or required by the spec.
7. Report total task count, per-story breakdown, and parallel opportunities.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Each task must include a clear action and a file path or artifact target.
- Tasks should be ordered so earlier items unblock later work.
- Keep tasks small enough to complete in a single implementation pass.
- The **last task** in every generated task list MUST be a retrospective review task: `- [ ] T<NNN> Run the retrospective skill to review all implemented changes for code quality and architectural decisions (specs/<identifier>/retrospective.md)`. This ensures a post-implementation review is always triggered after all work is complete.
