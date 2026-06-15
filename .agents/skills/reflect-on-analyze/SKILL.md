---
name: reflect-on-analyze
description: Research each finding from analyze.md, produce an evidence-based reflection, and apply corrective changes to spec/plan/tasks artifacts. Use after the analyze skill has produced an analyze.md report.
allowed-tools: Read, Grep, Glob, Edit
---

## Goal

Turn analysis findings into concrete, evidence-backed decisions and artifact corrections. This skill bridges the gap between identifying issues (analyze) and starting implementation (implement).

## Prerequisites

- An `analyze.md` file MUST exist in the feature's `specs/<identifier>/` folder.
- `spec.md`, `plan.md`, and `tasks.md` MUST also exist in the same folder.

## Steps

1. **Locate artifacts**: Find `analyze.md`, `spec.md`, `plan.md`, and `tasks.md` in the feature's `specs/<identifier>/` folder.
2. **Read analyze.md**: Load all findings, grouped by severity (HIGH → MEDIUM → LOW).
3. **Research each finding**:
   - For each finding, read the referenced source files (dbt SQL, Python modules, tests, schemas) to gather evidence.
   - Determine whether the finding is valid, partially valid, or a false positive.
   - For valid findings, decide on the corrective action (update spec, plan, tasks, or document as out-of-scope).
   - Record the evidence, reasoning, and decision.
4. **Produce `analyze-reflect.md`**: Write a reflection document in the same `specs/<identifier>/` folder with:
   - One section per finding (using the finding ID from analyze.md, e.g., F-001).
   - Subsections: Research, Decision, Artifact Changes.
   - A summary table at the end listing all artifact changes.
5. **Apply changes**: Edit `spec.md`, `plan.md`, and/or `tasks.md` to implement the decisions from step 4.
   - Make minimal, surgical edits — change only what the reflection calls for.
   - Do NOT rewrite entire sections; update specific lines.
6. **Verify consistency**: After all edits, scan for any new inconsistencies introduced by the changes.

## Rules

- Spec artifacts live in `./specs/<identifier>/`.
- Research MUST reference actual source files with line numbers — no assumptions.
- Decisions MUST be evidence-based. If the finding is a false positive, explain why with file references.
- Changes MUST be minimal — do not refactor or rewrite sections beyond what the finding requires.
- If a finding is out of scope, document it as such with a clear rationale rather than ignoring it.
- Respect repository governance (AGENTS.md) and existing conventions.
- Do NOT delete findings from analyze.md — the reflection documents the response, not a revision of the analysis.

## Output

- `analyze-reflect.md` in the feature's specs folder.
- Updated `spec.md`, `plan.md`, and/or `tasks.md` with corrective edits.
