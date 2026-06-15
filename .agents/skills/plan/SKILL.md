---
name: plan
description: Create an implementation plan and supporting design artifacts from a feature spec. Use after a spec is approved and ready for technical planning.
---

## Goal

Translate the approved specification into a technical plan and any required design artifacts.

## Steps

1. Locate the feature spec and determine the plan output location per repo conventions.
2. Use a plan template if the repo provides one; otherwise include:
   - Technical Context
   - Architecture/Components
   - Data Model
   - Interfaces/Contracts
   - Phases/Dependencies
   - Risks and Open Questions
   - Testing Strategy
3. Resolve unknowns or record them as clear questions for follow-up research.
4. Create supporting artifacts as needed (data-model.md, contracts/, quickstart.md).
5. Update any repo-specific agent context or documentation if required.
6. Report generated artifacts and readiness for task breakdown.

## Rules

- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
- Keep the plan consistent with the spec and repository governance.
- Prefer incremental, testable delivery phases.
- Use repo-relative paths in references (never absolute host paths).
