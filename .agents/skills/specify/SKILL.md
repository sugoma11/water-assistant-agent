---
name: specify
description: Create or update a feature specification from a natural language request. Use when turning requests into a clear, testable spec.
---

## Goal

Turn a feature request into a clear, testable specification written for non-technical stakeholders.

## Steps

1. Confirm the feature request text and identify key actors, actions, data, and constraints.
2. Determine the Linear identifier from the current branch name when it matches `username/identifier-title`; extract the `identifier` segment (e.g., `nikita/fin-283-...` -> `fin-283`). If the branch format differs or no identifier is present, ask the user for the identifier before proceeding.
3. Ensure the correct feature branch exists (follow repository branch naming rules or use the spec-branch skill).
4. Locate a spec template in the repo; if none exists, use a standard structure:
   - Overview/Context
   - Goals and Non-Goals
   - User Stories/Scenarios
   - Functional Requirements
   - Non-Functional Requirements
   - Data/Entities (if applicable)
   - Assumptions
   - Success Criteria (measurable)
   - Edge Cases/Error Handling
5. Make informed, industry-standard assumptions for unspecified details and record them explicitly.
6. Avoid implementation details (frameworks, APIs, code structure).
7. Save the spec using the repository's spec location conventions.
8. Report the spec path and readiness for clarification or planning.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Keep requirements testable and unambiguous.
- If critical choices are unclear, flag them for the clarify skill rather than blocking progress.
- Remove any template sections that do not apply (do not leave "N/A").
