# Hooks

`pre-commit` is one bash script that runs on WSL/Linux and on Windows (Git for
Windows runs hooks with its bundled bash). Install it with
`scripts/install-hooks.sh`.

## Why `core.hooksPath` and not the pre-commit framework

* `core.hooksPath` needs no per-repo config file, no Python virtualenv in each
  repo, and no network at commit time. The pre-commit framework would add a
  `.pre-commit-config.yaml` to every product repo - a visible change to repos
  this tool should not have to modify.
* The catch: a repo-local `core.hooksPath` **overrides** the global one. Repos
  that use husky point `core.hooksPath` at `.husky`, and some repos point it at
  their own `.git/hooks`. The global hook never fires in those repos.
* So the installer does both: it sets the global `core.hooksPath` as the baseline
  for every repo that has no local override, and it drops a `pre-commit` into the
  hook directory of each repo that does have one, chaining any hook already there
  as `pre-commit.local`.

## Two limits worth knowing

* **A tracked hook is never overwritten.** A husky repo sets
  `core.hooksPath` to `.husky`, and `.husky/pre-commit` is a file the repository
  tracks. Replacing it would show up as an uncommitted change in that project,
  so the installer skips it and prints the one line to add instead. Until
  someone commits that line, that repo is covered by CI only.
* **husky regenerates `.husky/_/`.** In husky repos whose
  `.husky/pre-commit` is not tracked, the hook goes into `.husky/_/pre-commit`, which is
  gitignored - no repository change - but husky rewrites that directory on
  `husky install` (which `prepare` runs on every `npm install`). Re-run
  `scripts/install-hooks.sh <repo>` after a dependency install there, or rely on
  CI.
