# dep-age-gate

Refuse dependency versions that were published less than 72 hours ago.

A compromised package is usually caught and unpublished within a day or two.
Waiting 72 hours before you install a brand-new version removes most of that
window. Some package managers can enforce this themselves; the rest cannot, and
none of them enforce it on `npm ci` / `--frozen-lockfile` installs. This repo
holds the settings for the ones that can, and a checker for everything else.

Python 3.10+, standard library only. Runs on WSL/Linux and on Windows.

```
dep-age-gate check npm:vitest@5.0.0
dep-age-gate audit --base origin/main
dep-age-gate init-uv ./my-project
```

---

## 1. What is gated where

`install gate` = the package manager itself refuses to resolve a too-young
version. `lock audit` = dep-age-gate reads the lockfile and fails. `CI` = the
GitHub Action runs the audit on every pull request.

| Ecosystem | Tool | Install gate (WSL) | Install gate (Windows) | Lock audit | CI |
|---|---|---|---|---|---|
| JS | npm 11.19.1 | `min-release-age=3` in `~/.npmrc` | same in `%USERPROFILE%\.npmrc` | `package-lock.json`, `npm-shrinkwrap.json` | yes |
| JS | pnpm 11.10.0 | `minimumReleaseAge: 4320` in `~/.config/pnpm/config.yaml` | same in `%LOCALAPPDATA%\pnpm\config\config.yaml` | `pnpm-lock.yaml` (v5/v6/v9) | yes |
| JS | bun 1.3.10 | `minimumReleaseAge = 259200` in `~/.bunfig.toml` | same in `%USERPROFILE%\.bunfig.toml` | `bun.lock`; `bun.lockb` fails closed | yes |
| JS | yarn 4.18.0 | `npmMinimalAgeGate: "72h"` in `~/.yarnrc.yml` | same in `%USERPROFILE%\.yarnrc.yml` | `yarn.lock` (v1 and berry) | yes |
| Python | pip | `uploaded-prior-to = P7D` in `~/.config/pip/pip.conf` | same in `%APPDATA%\pip\pip.ini` | `requirements*.txt` (`==` pins only) | yes |
| Python | uv 0.12.1 | per project `exclude-newer = "72h"` | same file, same key | `uv.lock` | yes |
| Python | poetry | none | none | `poetry.lock` | yes |
| Rust | cargo 1.91 | `~/bin/cargo` wrapper | `cargo.cmd` + `cargo.ps1` wrapper | `Cargo.lock` | yes |
| Java | Gradle | none on WSL (no gradle installed) | `gradle.cmd` + `gradle.ps1` wrapper | `gradle.lockfile` | yes |
| Java | Maven | none on WSL (no mvn installed) | `mvn.cmd` + `mvn.ps1` wrapper | `pom.xml` direct deps | yes |

### Units, per tool

Every tool picked a different unit. Copying a number from one config into
another silently gives the wrong window; that is how `minimum-release-age` sat
in `~/.npmrc` for two months doing nothing.

| Tool | Key | Unit | 72 hours is | Config file |
|---|---|---|---|---|
| npm | `min-release-age` | **days** (integer) | `3` | `.npmrc` |
| pnpm | `minimumReleaseAge` | **minutes** | `4320` | `config.yaml` (never `.npmrc`) |
| bun | `minimumReleaseAge` | **seconds** | `259200` | `.bunfig.toml` `[install]` |
| yarn ≥ 4.10 | `npmMinimalAgeGate` | **duration string**; a bare number is **minutes** | `"72h"` (or `4320`) | `.yarnrc.yml` |
| pip | `uploaded-prior-to` | **ISO-8601 duration** | `PT72H` (currently set to `P7D`) | `pip.conf` / `pip.ini` |
| uv | `exclude-newer` | **relative span or RFC 3339 date** | `"72h"` | `pyproject.toml` `[tool.uv]` |
| cargo | none | - | - | wrapper + audit |
| Gradle / Maven | none | - | - | wrapper + audit |

Sources, and what was tested rather than assumed:

* yarn - `npmMinimalAgeGate` is declared `SettingsType.DURATION` with default
  `"1d"` in `packages/plugin-npm/sources/index.ts`.
  <https://yarnpkg.com/configuration/yarnrc#npmMinimalAgeGate>
  Measured on yarn 4.18.0 on 2026-09-05 against real registry data: `"1d"`
  blocks a version published 15.5 h earlier; `"24h"` allows a 39 h old version
  and `"48h"` blocks it; a bare `20` allows the 15.5 h version and a bare
  `4320` blocks it, so a bare number is minutes and the suffixes are honoured.
  A blocked version reports `YN0016: ... All versions satisfying "X" are
  quarantined` and yarn exits 1. `npmPreapprovedPackages` is the exemption list.
* uv - `exclude-newer` accepts RFC 3339 timestamps, "friendly" durations
  (`"72 hours"`, `"1 week"`) and ISO-8601 durations (`"PT72H"`, `"P7D"`).
  <https://docs.astral.sh/uv/reference/settings/#exclude-newer>
  Measured on uv 0.12.1: `"72h"`, `"72 hours"`, `"3d"` and `"PT72H"` are all
  accepted; uv writes `exclude-newer-span = "PT72H"` into `uv.lock` plus a
  compatibility line `exclude-newer = "0001-01-01T00:00:00Z"`. With the setting,
  `uv lock` resolved `boto3` to 1.43.86; without it, to 1.43.89, which was
  10 hours old at the time.

### Why uv goes in the project, never in a global `uv.toml`

`exclude-newer` changes which versions uv resolves, and uv records the span in
every `uv.lock` it writes. Set globally, it would rewrite the lockfile of every
Python project on the machine, including repos owned by other people, and the
reason would be invisible inside those repos: their `pyproject.toml` would say
nothing while their lockfile grew an `exclude-newer-span` line. Put it in the
project that wants it:

```toml
[tool.uv]
exclude-newer = "72h"
```

`dep-age-gate init-uv <project>` writes exactly that (dry run by default,
`--write` to apply).

---

## 2. Commands

### `check` - one or more versions, straight from the registries

```
$ dep-age-gate check npm:vitest@5.0.0 npm:vitest@4.1.11
STATUS  PACKAGE            PUBLISHED (UTC)              AGE     SOURCE  NOTE
------  -----------------  ---------------------------  ------  ------  ----
FAIL    npm:vitest@5.0.0   2026-09-03T12:24:30.312000Z  1d17h   <cli>
PASS    npm:vitest@4.1.11  2026-08-18T14:27:07.240000Z  17d15h  <cli>

minimum age 3d0h (259200s) - pass 1, fail 1, unknown 0
$ echo $?
1
```

Spec forms: `npm:<pkg>@<ver>`, `pypi:<pkg>@<ver>`, `crates:<crate>@<ver>`,
`maven:<groupId>:<artifactId>:<version>`.

### `audit` - what a change adds to a lockfile

`audit` compares two versions of each lockfile and checks only what is new or
changed, so a 742-entry `package-lock.json` costs four lookups when a pull
request bumps four packages.

```
dep-age-gate audit                       # staged changes vs HEAD (the pre-commit hook)
dep-age-gate audit --base origin/main    # working tree vs a ref (CI)
dep-age-gate audit path/to/uv.lock       # one file vs its committed version
dep-age-gate audit --all Cargo.lock      # every entry, ignore history
dep-age-gate audit --lock Cargo.lock --before /tmp/snap  # the wrappers' mode
```

Useful flags: `--min-age 72h`, `--json`, `-v` (also print passes and skips),
`--allow <pkg>@<ver> --reason "<why>"`, `--allow-binary-lock`, `--jobs N`,
`--no-cache`, `--exclude <glob>`.

Lockfiles that are test data rather than dependencies you install go in
`.dep-age-gate-ignore` in the repository root, one glob per line:

```
# this repo's own fixtures
tests/fixtures/**
```

`--allow` needs `--reason`, and the reason is printed in the report:

```
STATUS   PACKAGE             SOURCE             NOTE
ALLOWED  npm:eslint@10.10.0  package-lock.json  CVE-2026-1234 fix, tarball diffed, npm provenance repo eslint/eslint
```

### `init-uv`

```
dep-age-gate init-uv path/to/project           # prints a diff, writes nothing
dep-age-gate init-uv path/to/project --write   # applies it
```

### Formats read

`package-lock.json` (v1/v2/v3), `npm-shrinkwrap.json`, `pnpm-lock.yaml`
(v5/v6/v9), `bun.lock`, `bun.lockb` (fails closed), `yarn.lock` (v1 and berry),
`uv.lock`, `poetry.lock`, `requirements*.txt` / `*.in`, `Cargo.lock`,
`gradle.lockfile`, `pom.xml`.

Publish dates come from `registry.npmjs.org` (`time[version]`),
`pypi.org/pypi/<pkg>/<ver>/json` (`urls[].upload_time_iso_8601`),
`crates.io/api/v1/crates/<crate>/versions` (`versions[].created_at`) and
`search.maven.org/solrsearch` (`timestamp`, ms), falling back to the
`Last-Modified` of the `.pom` on repo1.maven.org. Answers are cached for 24 h in
`~/.cache/dep-age-gate` (`%LOCALAPPDATA%\dep-age-gate\cache` on Windows).

Entries with no registry publish date are reported as skips, never as passes:
workspace links, `git+`, `file:`, `link:`, `portal:`, `workspace:`, `patch:`
protocols, Cargo path members, poetry git sources, `pom.xml` versions inherited
from a parent or BOM, and unresolvable `${property}` versions. Run with `-v` to
list them.

---

## 3. Bypass procedure

The gate is a delay, not a verdict. When you genuinely need a version that is
younger than 72 hours, audit it first. All four steps, every time:

1. **Install scripts.** Does the package run `preinstall` / `install` /
   `postinstall`? `npm view <pkg>@<ver> scripts`. `ignore-scripts=true` is
   already set for npm and pnpm here, but a package's `bin` still runs when you
   execute it.
2. **Tarball diff against the previous version.** `npm pack <pkg>@<ver>` and
   `npm pack <pkg>@<previous>`, unpack both, `diff -r`. Look for new network
   calls, new files, obfuscated or minified additions in a source package.
3. **Trusted publishing / provenance.** `npm view <pkg>@<ver> dist.attestations`
   or the "Provenance" block on the package page. No provenance on a package
   that normally has it is itself the signal.
4. **Provenance repository matches.** The attested repo and workflow must be the
   package's real source repository, not a fork or a lookalike.

Then bypass, in order of preference:

```
dep-age-gate audit --allow npm:<pkg>@<ver> --reason "<what you checked>"   # recorded
ALLOW_YOUNG_DEPS=1 <command>                                               # wrappers and hook, prints a warning
npm  install --min-release-age=0 <pkg>                                     # one command, one tool
pnpm add --minimum-release-age=0 <pkg>
bun  add --minimum-release-age=0 <pkg>
yarn add <pkg>   # with npmPreapprovedPackages: ["<pkg>"] in .yarnrc.yml
uv   lock --exclude-newer-package '<pkg>=false'
git commit --no-verify                                                     # last resort, records nothing
```

---

## 4. Known holes

* **`npm ci`, `pnpm install --frozen-lockfile`, `bun install --frozen-lockfile`,
  `yarn install --immutable` install exactly what the lockfile says.** The
  install-time gates only apply while *resolving* a version. This is why the
  lockfile audit and the CI job exist: the young version is caught at the commit
  that introduces it, not at install time.
* **A repo that pins `packageManager: npm@10.x`** gets no npm gate.
  `min-release-age` needs npm ≥ 11.10.0; npm 10 ignores it without a warning.
  Move the pin to npm ≥ 11.10.0 (and widen `engines.npm` if the repo uses
  `engine-strict`). The CI audit still covers such a repo.
* **A repo that pins `packageManager: yarn@4.3.1`** (or any yarn < 4.10) gets no
  yarn gate. `npmMinimalAgeGate` did not exist yet.
* **`bun.lockb` cannot be read.** It is an undocumented binary format. The audit
  fails closed rather than guessing. Convert with
  `bun install --save-text-lockfile`.
* **`requirements.txt` ranges cannot be checked.** Only `name==version` pins
  name one version. Compile them (`uv pip compile`, `pip-compile`).
* **`pom.xml` covers direct dependencies with an explicit version.** Transitive
  dependencies and versions inherited from a parent POM or a BOM have no version
  in the file to check. Gradle projects need dependency locking turned on
  (below) before there is anything to audit.
* **Nothing here verifies package *content*.** A package can be malicious and
  four days old. The 72-hour window buys time for other people to notice; it is
  not a guarantee.
* **`~/bin` must stay ahead of `~/.cargo/bin` on `PATH`,** or the cargo wrapper
  is skipped. `command -v cargo` must print `~/bin/cargo` (expanded).
* **The cargo wrapper checks after the fact.** cargo has already downloaded the
  crate into `~/.cargo/registry` by the time the lockfile is audited. The
  wrapper prevents the dependency from being *kept*, not from being fetched.
  Nothing was compiled or executed - build scripts only run on `cargo build`.
* **Transitive crates are usually the ones that fail.** cargo resolves
  transitive dependencies to the newest compatible version, so a `cargo add` of
  a two-year-old crate can still pull in a crate published this morning. On
  2026-09-05, `cargo add serde@1.0.200 --no-default-features` pulled
  `syn@3.0.5`, ten hours old.

---

## 5. Gradle dependency locking

Gradle writes no lockfile unless you ask it to, and the wrapper has nothing to
audit without one. In `build.gradle`:

```groovy
dependencyLocking {
    lockAllConfigurations()
}
```

or `build.gradle.kts`:

```kotlin
dependencyLocking {
    lockAllConfigurations()
}
```

Then generate the lockfiles once:

```
gradle dependencies --write-locks
```

This writes `gradle.lockfile` next to each project's build file. Commit them.

---

## 6. Layout

```
dep_age_gate/        the package - CLI, registry clients, cache, parsers
wrappers/            cargo (bash, WSL) + cargo/gradle/gradlew/mvn (PowerShell, Windows)
hooks/pre-commit     one bash hook, runs on WSL and on Git for Windows
action/              composite GitHub Action + the dep-age-gate.yml workflow template
scripts/             install-hooks.sh, install-workflow.sh, stage-windows.sh
tests/               pytest suite with fixture lockfiles and a fake registry
```

Run the tests with `python -m pytest`.

## 7. CI

`action/dep-age-gate.yml` runs on `pull_request` and on pushes to the deploy
branches, with `permissions: contents: read`, and pins `actions/checkout` and
`actions/setup-python` by commit SHA. Install it into one repo with:

```
scripts/install-workflow.sh /path/to/repo
```

The workflow references the action by commit SHA:
`th3nolo/dep-age-gate/action@<sha>`. The SHA comes from `git rev-parse HEAD` of
this repo unless you pass `--sha`. Pin a tagged commit and update it on purpose;
never `@main`.

If you host a private copy of this repo inside your GitHub organization, pass
`--org YourOrg`. A private action is only usable by repositories in the same
org, and the copy needs the repository setting "Actions access: accessible from
repositories in the organization".

The push branches default to the deploy branches the target repo actually has -
`dev`, `staging` and `production` when present, plus `master` or `main` when
that is the default branch - and `--push-branches "a b c"` overrides that.

It writes `.github/workflows/dep-age-gate.yml` and stops - no `git add`, no
commit, no push. Review the diff and open a pull request yourself.

## License

MIT.
