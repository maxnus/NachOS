# Working on nachOS with Claude Code

@../AGENTS.md

`AGENTS.md` holds the rules for writing code here. This file holds how the repo owner works with an agent, and
`.claude/plans/next-steps.md` holds the work in progress: read it before starting on anything.

## Pace

- **One step at a time.** Finish the agreed step and show the result with its verification. Where the next step is
  plainly the obvious one, go ahead with it and say so; otherwise offer it and wait.
- **Plan a design change with the owner before building it.** The owner changes designs when a version grows too
  complicated; a decision recorded in `.claude/plans/next-steps.md` stands until the owner reverses it.
- Where the owner's feedback says "do not change any code until we have discussed the verdicts", discuss first.

## Commits, pushes and pull requests

- **Once pushed, a commit is frozen**: answer review with a new commit appended, never `--amend`, a fixup rebase
  that reaches it, or a force-push, since that breaks GitHub's "changes since your last review". Tidying history
  is the owner's call; offer a squash-merge at the end instead.
- Branch off `origin/main` after a fetch; a local `main` may be stale.
- Measurements, and which run proved what, go in the commit message and the PR body.

## Reviews

- **Walking through findings**: one `AskUserQuestion` per finding, with everything inside the question text, since
  text written before the question is not shown with it: file and line, the original snippet, the owner's comment
  where there is one, and the verdict (confirmed, partly, not recommended; new in this PR or already there). Put
  proposed code in the options' previews. Record each decision in a scratch file as you go, and apply them all only
  after the walk-through.
- A local diff review with Plannotator, where it is installed: `plannotator review --git --base origin/main
  --diff-type since-base --json`, run in the background, since it waits for the owner.

## Writing docs and comments

- **A docstring states the contract, and nothing that argues for the design.** Delete any sentence that explains
  why the code is shaped this way (a name, a method rather than a property, where something lives); keep what a
  caller cannot infer (an edge case, a rounding rule, what it raises). An answer given in chat does not go into
  the file as well; if it is worth keeping, it goes in the commit message. After a round of doc edits, list the
  multi-line docstrings and check each:

  ```
  uv run python -c "import ast,pathlib
  for p in sorted(pathlib.Path('src').rglob('*.py')):
      t = ast.parse(p.read_text(encoding='utf-8'))
      for n in ast.walk(t):
          if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)):
              d = ast.get_docstring(n)
              if d and len(d.splitlines()) > 1: print(p, getattr(n, 'name', ''))
          if isinstance(n, ast.ClassDef):
              for a, b in zip(n.body, n.body[1:]):
                  if isinstance(a, ast.AnnAssign) and isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant):
                      if isinstance(b.value.value, str) and len(b.value.value.splitlines()) > 1:
                          print(p, n.name + '.' + ast.unparse(a.target))"
  ```

- **`AGENTS.md` takes rules for writing code only**, a line or two each: never a statement of what the library does
  for a bot, which belongs in the user docs if anywhere. Before adding a line, ask whether an agent that skipped it
  would get something wrong.
- **Where the game's answer is a matter of perspective, document what the API reports** and move on: a pylon reads
  unpowered, though it always stands in its own field. Keep "Not understood yet" in `docs/game-behavior.md` for
  readings that contradict each other or could not be observed.

## Running things

- `uv run pyright` with no path argument: a path makes it ignore `include` and walk the whole tree.
- The sweeps in `tools/` and `uv run pytest -m integration` start the game and take minutes.

## Code the owner likes

- A filter takes one value or a collection of them (`UnitTypeId | Collection[UnitTypeId]`) and normalizes inside.
- A value derived from a data object is a property on that object, not a lookup its callers repeat.
- Short functions: extract a named helper rather than growing the caller.
