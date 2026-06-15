---
name: pr-review-reply
description: "Address all review conversations on a GitHub PR: fix code issues or reply with rationale, then verify zero unresolved threads remain. Use when the user shares a PR URL and asks to handle review comments."
argument-hint: "[pr-url]"
---

# PR Review Reply

## Goal

Process every unresolved review thread on a pull request — fix valid issues in code and reply to explain design decisions — until no unresolved threads remain and every thread has at least one reply.

## Prerequisites

- The working tree is on the PR's head branch (or can be switched to it).
- `/usr/bin/gh` is authenticated (`/usr/bin/gh auth status`).

## Inputs

- **PR URL** — e.g. `https://github.com/ORG/REPO/pull/123`. Extract `owner`, `repo`, and `pr_number`.

## Workflow

### 1. Parse the PR URL

Extract `owner`, `repo`, and `pr_number` from the URL pattern `github.com/{owner}/{repo}/pull/{pr_number}`.

### 2. Fetch all unresolved threads

Use the GitHub GraphQL API via `/usr/bin/gh`:

```bash
/usr/bin/gh api graphql -f query='
{
  repository(owner: "OWNER", name: "REPO") {
    pullRequest(number: PR_NUMBER) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 10) {
            nodes {
              databaseId
              path
              line
              body
              author { login }
              createdAt
            }
          }
        }
      }
    }
  }
}'
```

Filter to threads where `isResolved == false`. Record each thread's:
- GraphQL `id` (for resolving)
- First comment's `databaseId` (for replying via REST)
- `path`, `line`, `body` (for understanding the issue)
- Whether it has replies already

### 3. Classify each thread

For each unresolved thread, read the comment body and the referenced file/line. Classify as:

| Classification | Action |
|---|---|
| **Valid bug / missing code** | Fix the code, commit, reply with commit SHA |
| **Valid suggestion but out of scope** | Reply explaining it will be tracked separately |
| **Design decision / intentional** | Reply with rationale citing architecture docs or code evidence |
| **Vendored / upstream code** | Reply that it is upstream code we do not modify |
| **Already fixed (outdated diff)** | Reply confirming it was fixed, cite commit if possible |

### 4. Fix code issues

For threads classified as valid bugs:

1. Read the referenced file and understand the issue.
2. Make the minimal fix.
3. Run relevant linters/tests if available (`just lint`, `just test`, or file-scoped variants).
4. Stage and commit with a descriptive message referencing the review feedback.
5. Push the branch.

Group related fixes into a single commit when they address the same concern.

### 5. Reply to every thread

For each unresolved thread, post a reply using the REST API:

```bash
/usr/bin/gh api 'repos/OWNER/REPO/pulls/PR_NUMBER/comments/COMMENT_DB_ID/replies' \
  -f body='Your reply text'
```

Reply content guidelines:
- **Fixed**: `"Fixed in {sha}. {one-sentence description of what changed}."`
- **By design**: `"Intentional. {evidence/rationale}."` — cite specific files, line numbers, or architecture docs.
- **Out of scope**: `"Valid point. Tracking separately — not in scope for this PR."`
- **Vendored**: `"This is upstream vendored code; not modified in this repo."`

### 6. Resolve threads (if the author is also the pusher)

After replying, attempt to resolve each thread via GraphQL:

```bash
/usr/bin/gh api graphql -f query='
  mutation { resolveReviewThread(input: {threadId: "THREAD_NODE_ID"}) { thread { isResolved } } }
'
```

If the mutation fails (permissions), skip silently — the reply is sufficient.

### 7. Validate: zero unresolved threads

Re-fetch all threads (repeat step 2). Assert:
- Every thread has `isResolved == true` OR has at least one reply from us.
- No thread has zero replies.

If any thread is still unresolved without a reply, go back to step 3 for that thread.

### 8. Report summary

Print a table:

```
| # | File | Classification | Action |
|---|------|---------------|--------|
| 1 | src/foo.py:42 | Fixed | abc1234 |
| 2 | src/bar.py:10 | By design | Replied |
| 3 | vendor/lib.js:5 | Vendored | Replied |

✅ All N threads addressed. M fixed, K replied.
```

## Rules

- Always use `/usr/bin/gh` (not bare `gh`) to avoid PATH conflicts with other packages.
- Never modify vendored or upstream dependency files to fix review comments about them — reply instead.
- Do not auto-resolve threads you did not author or that contain unaddressed feedback from humans (non-bot reviewers).
- Commit messages must include `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Keep code fixes minimal — do not refactor beyond what the review comment asks for.
- If a fix requires a dependency change (`uv add`), do not run it — document the required command and request human execution per AGENTS.md §X.
- Pipe all `/usr/bin/gh` output through `| cat` or use `--jq` to avoid interactive pagers blocking execution.
