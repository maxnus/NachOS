# Releasing

A release is a GitHub release whose tag is `v` and the version, as `v0.1.0`. Publishing one starts the `release`
workflow, which builds the tag, tests it, uploads it to PyPI and then publishes its reference to GitHub Pages through
`api-reference`. The version is read from the tag, and nothing in the repository is edited to make one.

## Once, before the first release

- **PyPI**, signed in as the account that is to own `sc2nachos`: under Your account, Publishing, add a pending
  publisher for GitHub, with the project name `sc2nachos`, the owner `maxnus`, the repository `NachOS`, the workflow
  `release.yml` and the environment `pypi`. The first upload creates the project, and the pending publisher becomes
  its trusted publisher, so no token is stored anywhere.
- **The `pypi` environment**, in the repository's Settings, Environments: create it, and under its deployment branches
  and tags allow the tags `v*` alone. A required reviewer there makes every upload wait for an approval in the run.
- **GitHub Pages**, in Settings, Pages: GitHub Actions as its source. Then let the `github-pages` environment deploy
  the tags `v*` too, since by default it allows the default branch alone.

## Choosing the version

Until 1.0, a release that renames, removes or changes anything a bot uses goes up a minor version, 0.2.0 after 0.1.x,
and one that only adds or fixes goes up a patch, 0.1.1, so a bot that asks for `~=0.1.0` takes fixes without its code
breaking. A pre-release ends in `rc` and a number, `v0.2.0rc1`, and its GitHub release is marked a pre-release, or the
run refuses it: PyPI takes it, the reference is left as it is, and pip installs it only when asked for that version,
or while PyPI has nothing but pre-releases. The first time, a pre-release, `v0.1.0rc1`, tries the whole workflow out,
and is what `pip install sc2nachos` installs until 0.1.0.

## Making one

1. On the commit to release, normally `main`, run the tests that play a game, which no workflow runs by itself:
   `uv run pytest -m integration -n 8` with the game installed, eight games at once, or the `sc2` workflow from the
   Actions tab with `-m` as the script and `pytest -m integration` as the arguments.
2. Publish the release of that commit, with notes listing the pull requests merged since the last one:
   `gh release create v0.1.0 --target <commit> --generate-notes`, with `--prerelease` for a pre-release. Say in the
   notes what a bot has to change. Releases, Draft a new release, on GitHub does the same. Either makes a plain tag;
   never tag with `git tag -a`, since a commit carrying an annotated pre-release tag builds that pre-release's version
   for every later tag on it, and the run refuses their releases.
3. Follow the `release` run, and approve it in the `pypi` environment if it waits. Once it is done,
   `pip install sc2nachos==0.1.0` installs it, and the reference shows it.

## When it goes wrong

A run that fails before its upload, on a tag that is not the version as PyPI writes it, a pre-release mark that does
not match it, or a test that fails, leaves PyPI and the reference as they were: delete the release and its tag,
`gh release delete v0.1.0 --cleanup-tag`, and make it again.

PyPI never takes a version twice, even once it is deleted, so a broken release is followed by the next version, never
replaced. Yank the broken one on PyPI, under the project's Manage, Releases: pip then skips it unless asked for it
exactly.
