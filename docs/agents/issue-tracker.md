# Issue tracker: GitHub

Issues and specs live at https://github.com/sabrinanda/verif/issues.
Use the `gh` CLI for all operations.

The local Git remote `origin` points to `https://github.com/sabrinanda/verif.git`. Always pass
`--repo sabrinanda/verif` to `gh issue` and `gh pr` commands;
for `gh api`, use the `repos/sabrinanda/verif/` endpoint prefix.

## Conventions

- Create: `gh issue create --repo sabrinanda/verif --title "..." --body "..."`
- Read: `gh issue view <number> --repo sabrinanda/verif --comments`
- List: `gh issue list --repo sabrinanda/verif --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --repo sabrinanda/verif --body "..."`
- Apply/remove labels: `gh issue edit <number> --repo sabrinanda/verif --add-label "..." --remove-label "..."`
- Close: `gh issue close <number> --repo sabrinanda/verif --comment "..."`

For multi-line bodies, use `--body-file <path>`. Filter lists by
`--label` and `--state` as needed. Use `triage-labels.md` for label names.

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub shares issue and PR numbers. Resolve an ambiguous reference
with `gh pr view <number> --repo sabrinanda/verif`, then fall back
to `gh issue view <number> --repo sabrinanda/verif`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --repo sabrinanda/verif --comments`.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing Notes,
  Decisions-so-far, and Fog.
- Child ticket: link it as a GitHub sub-issue. If sub-issues are
  unavailable, use a task list in the map and `Part of #<map>`
  in the child body. Label it `wayfinder:<type>` using
  research, prototype, grilling, or task.
- Blocking: use native GitHub issue dependencies via
  `gh api --method POST repos/sabrinanda/verif/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`.
  Obtain the database ID with
  `gh api repos/sabrinanda/verif/issues/<number> --jq .id`.
  If dependencies are unavailable, use `Blocked by: #<n>, #<n>`
  in the child body.
- Frontier: choose the first open, unassigned child in map order
  with no open blockers.
- Claim: assign the ticket using
  `gh issue edit <number> --repo sabrinanda/verif --add-assignee @me`
  before starting work.
- Resolve: comment with the answer, close the child, then append
  a summary and link to the map's Decisions-so-far.
