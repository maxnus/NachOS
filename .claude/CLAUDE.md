# Working on nachOS with Claude Code

@../AGENTS.md

`AGENTS.md` holds the rules for writing code and docs here, for any agent. This file holds how the repo owner works
with an agent, and `.claude/plans/next-steps.md` holds the work in progress. Read it before starting anything.

## Pace

- **One step at a time.** Finish the agreed step and show the result with its verification. If the next step is
  plainly the obvious one, go ahead with it and say so; otherwise offer it and wait.
- **Plan a design change with the owner before building it.** The owner changes a design when a version grows too
  complicated. A decision recorded in `.claude/plans/next-steps.md` stands until the owner reverses it.
- When the owner's feedback says "do not change any code until we have discussed the verdicts", discuss first.

## Commits, pushes and pull requests

- **A pushed commit is frozen.** Answer review with a new commit on top, never `--amend`, a fixup rebase that
  reaches it, or a force-push: those break GitHub's "changes since your last review". Tidying history is the
  owner's call; offer a squash-merge at the end instead.
- Branch off `origin/main` after a fetch; a local `main` may be stale.
- Measurements, and which run proved what, go in the commit message and the PR body.

## Reviews

- **Review every pull request you open** before calling it ready: read its whole diff on GitHub, for correctness,
  tests, docs and generated files, and report the findings to the owner, or say there are none. The
  `claude-code-review` workflow posts only high-confidence bugs and does not replace this.
- **Walking through findings**: one `AskUserQuestion` per finding, with everything in the question text, since text
  written before the question is not shown with it: file and line, the original snippet, the owner's comment if
  there is one, and the verdict (confirmed, partly, not recommended; new in this PR or already there). Put proposed
  code in the options' previews. Record each decision in a scratch file as you go, and apply them all only after
  the walk-through.
- A local diff review with Plannotator, where it is installed: `plannotator review --git --base origin/main
  --diff-type since-base --json`, run in the background, since it waits for the owner.
