---
name: clarify
description: Reduce ambiguity in a feature specification via targeted questions. Use when requirements are unclear or need stakeholder decisions.
allowed-tools: Read, Grep, Glob, Edit
---

## Goal

Identify the highest-impact gaps in the current specification and capture clarifications directly in the spec.

## Steps

1. Locate the active spec file using repository conventions.
2. Scan for ambiguity and missing decisions across scope, data, UX, non-functional needs, integrations, edge cases, constraints, and terminology.
3. Prioritize up to 5 questions that materially affect architecture, validation, or user experience.
4. Ask one question at a time; prefer multiple-choice with a recommended option when possible.
5. After each answer, update the spec in the most relevant section and add a dated Clarifications subsection.
6. Validate that the updated spec has no contradictions or unresolved placeholders.
7. Report the number of questions answered, updated sections, and suggested next step.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Do not ask about implementation details unless required to resolve functional ambiguity.
- Keep answers short and testable.
- Stop early if remaining questions are low-impact.
