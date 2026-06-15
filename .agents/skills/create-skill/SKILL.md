---
name: create-skill
description: Create a new agent skill from scratch with proper structure, frontmatter, and best practices. Use when adding a new skill to .agents/skills/.
argument-hint: "[skill-name]"
allowed-tools: Read, Glob, Bash(mkdir *)
---

# Create Skill

## Goal

Scaffold a new agent skill that follows repository conventions and Anthropic's authoring best practices.

## Steps

1. Get the skill name from the user. Convert to kebab-case if needed.
2. Create the directory `.agents/skills/<skill-name>/`.
3. Draft the `SKILL.md` using the structure guide and frontmatter reference below.
4. If the skill needs supplementary content (lookup tables, templates, examples), create a `references/` subdirectory and extract that content into focused files.
5. Add a reference table in `SKILL.md` pointing to each supplementary file with a description and load guidance.
6. Confirm the skill is discoverable by listing `.agents/skills/<skill-name>/`.

## Structure Guide

### Directory Layout

```text
.agents/skills/<skill-name>/
├── SKILL.md              # Required — skill definition
└── references/           # Optional — supplementary files
    ├── templates.md       #   output templates, schemas
    ├── examples.md        #   before/after examples
    └── lookup-tables.md   #   error codes, mappings
```

### SKILL.md Skeleton

```markdown
---
name: <skill-name>
description: "<what it does>. Use when <trigger condition>."
argument-hint: "[arg]"              # omit if no arguments
allowed-tools: Read, Grep, Glob    # omit for unrestricted; restrict when possible
---

## Goal

<One or two sentences: what this skill achieves.>

## Steps

1. <First action>
2. <Second action>
3. ...

## Rules

- <Constraint or guardrail>
- <What NOT to do>
```

### Choosing Sections

| Section | When to include |
|---------|-----------------|
| **Goal** | Always — the purpose in 1-2 sentences |
| **Steps** | Always — numbered, deterministic workflow |
| **Rules** | Always — constraints, guardrails, non-negotiables |
| **Prerequisites** | When the skill depends on prior artifacts or state |
| **Inputs to ask for** | When the skill needs user decisions before starting |
| **Reference Files** | When supplementary files exist in `references/` |
| **Output** | When the skill produces specific files or artifacts |

## Frontmatter Reference

| Field | Required | Purpose |
|-------|:--------:|---------|
| `name` | ✅ | Kebab-case identifier matching the directory name |
| `description` | ✅ | What it does AND when to use it — this is the trigger signal |
| `argument-hint` | ❌ | Autocomplete hint (e.g., `[file-path]`, `[phase/task-range]`) |
| `allowed-tools` | ❌ | Restrict tool access for safety. Examples: `Read`, `Bash(git *)`, `Read, Grep, Glob, Edit` |
| `disable-model-invocation` | ❌ | Set `true` to prevent auto-loading; skill triggers only via `/name` |
| `user-invocable` | ❌ | Set `false` to hide from `/` menu; use for background knowledge |
| `context` | ❌ | Set `fork` to run in an isolated subagent context |

## Best Practices

### Description is the router

The `description` field determines when the agent picks this skill. Include both *what* and *when*:

- ❌ `"Format markdown files"` — vague trigger
- ✅ `"Format a markdown file by running rumdl fmt. Use when a markdown file needs formatting, after editing specs, plans, or documentation."` — clear trigger

### Progressive disclosure

Keep `SKILL.md` body concise (under 500 lines). When content grows large:

1. Extract lookup tables, examples, and templates into `references/` files.
2. Add a reference table in `SKILL.md` with file descriptions:

   ```markdown
   ## Reference Files

   | File | Content |
   |------|---------|
   | `references/error-codes.md` | Error code lookup tables and before/after examples |

   Load `references/error-codes.md` when you need the recommended fix for a specific error code.
   ```

3. Tell the agent *when* to load each file — not just that it exists.

### Restrict tools when possible

Use `allowed-tools` to sandbox the skill. Grant only what is needed:

- Read-only analysis: `Read, Grep, Glob`
- File editing: `Read, Grep, Glob, Edit`
- Git operations: `Bash(git *), Read, Glob`
- Specific CLI: `Bash(rumdl *), Read`

### Rules are guardrails

Rules state boundaries and non-negotiables — what NOT to do:

- ✅ `"Do not modify source code — only produce the report."`
- ✅ `"Never suppress lint warnings without human approval."`
- ❌ `"Run the linter."` — this belongs in Steps, not Rules

### Spec artifacts convention

If the skill reads or writes spec artifacts, include this rule:

```text
- Spec artifacts (spec.md, plan.md, tasks.md, and related files) live in ./specs.
```

## Rules

- Skill name MUST be kebab-case, matching the directory name.
- `SKILL.md` MUST have frontmatter with at least `name` and `description`.
- Description MUST include both purpose and trigger condition ("Use when...").
- Do not create skills that duplicate existing skill functionality — check `.agents/skills/` first.
- Reference files go in `references/` only; do not nest deeper.
- Follow repo-relative paths everywhere (never absolute host paths).
