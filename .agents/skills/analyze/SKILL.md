---
name: analyze
description: Analyze spec, plan, and tasks for consistency, coverage, and ambiguity. Use when reviewing specification artifacts before implementation.
allowed-tools: Read, Grep, Glob
---

## Goal

Surface high-signal issues across specification, plan, and task artifacts before implementation.

## Steps

1. Locate spec.md, plan.md, and tasks.md using repository conventions.
2. Load only relevant sections needed for analysis (requirements, stories, phases, tasks).
3. Build an internal inventory of requirements, stories, and tasks.
4. Check for duplication, ambiguity, underspecification, coverage gaps, and inconsistency.
5. Write the report to `analyze.md` in the same `specs/<identifier>/` folder as the other artifacts.
6. The report MUST include:
   - A coverage matrix (requirement × artifact presence table).
   - Findings grouped by severity (🔴 HIGH, 🟡 MEDIUM, 🟢 LOW), each with a stable ID (F-001, F-002, …), location, issue description, impact, and recommendation.
   - Metrics summary (requirements covered, stories covered, edge-case coverage, finding counts by severity).
   - Suggested next actions ordered by priority.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Do not modify spec.md, plan.md, or tasks.md — only produce `analyze.md`.
- Respect repository governance or constitution documents if present.
- Focus on actionable findings; avoid stylistic feedback.
- Each finding MUST reference concrete file paths and line numbers as evidence.
