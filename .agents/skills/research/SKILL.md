---
name: research
description: Create a research.md proposal for a feature without relying on other research files. Use when drafting schema/SQL proposals or documenting decisions.
allowed-tools: Read, Grep, Glob
---

## Goal

Produce a concise research.md that captures proposal decisions (schema/queries) and alternatives, using a consistent template without needing to read other research artifacts.

## Steps

1. Locate the feature spec in `./specs/<identifier-*>/spec.md` and the plan in `./specs/<identifier-*>/plan.md` if present.
2. Extract the user’s explicit request for research content (e.g., schema proposals, SQL queries, benchmarks).
3. Draft research.md using the standard template below; do NOT scan other research files unless the user explicitly requests it.
4. Include a clear statement that the content is a proposal and may change during implementation.
5. If schema or SQL are requested, provide a concrete proposal with assumptions and note any open questions.
6. Save the file to `./specs/<identifier-*>/research.md` and report the path.

## Standard Template

- Title: `# Research: <Feature Name>`
- Proposal disclaimer paragraph (must include: “proposal only” + “may change during implementation”)
- Decisions/Proposals section(s)
- Alternatives considered (brief)
- Open questions (only if needed)

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, research.md) live in `./specs`.
- Use existing spec terminology; avoid implementation details not requested.
- Keep research concise and structured; prefer bullet lists.
- Do not import or reference other research files unless explicitly asked.
