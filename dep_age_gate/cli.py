"""Command line entry point."""

import argparse
import os
import sys
from datetime import datetime, timezone

from dep_age_gate import __version__, audit as audit_mod, report, uvinit
from dep_age_gate.cache import Cache
from dep_age_gate.duration import DurationError, humanize, parse_duration
from dep_age_gate.model import ECOSYSTEMS
from dep_age_gate.registry import RegistryClient
from dep_age_gate.spec import SpecError, parse_spec

DEFAULT_MIN_AGE = os.environ.get("DEP_AGE_GATE_MIN_AGE", "72h")
BYPASS_ENV = "ALLOW_YOUNG_DEPS"


def _min_age(value):
    try:
        return parse_duration(value)
    except DurationError as exc:
        raise SystemExit(f"dep-age-gate: {exc}")


def _build_allow(entries, reason):
    allow = {}
    if not entries:
        return allow
    if not reason:
        raise SystemExit(
            "dep-age-gate: --allow requires --reason; the reason is printed in the report"
        )
    for entry in entries:
        if ":" in entry and entry.split(":", 1)[0].lower() in (
            "npm", "pypi", "pip", "crates", "cargo", "maven", "mvn", "gradle",
            "node", "js", "py", "python", "rust", "java", "cratesio",
        ):
            dep = parse_spec(entry)
            allow[(dep.ecosystem, dep.name, dep.version)] = reason
            continue
        at = entry.rfind("@")
        if at <= 0:
            raise SystemExit(f"dep-age-gate: --allow {entry!r} is not <name>@<version>")
        name, version = entry[:at], entry[at + 1:]
        for ecosystem in ECOSYSTEMS:
            allow[(ecosystem, name, version)] = reason
    return allow


def _client(args):
    cache = Cache(enabled=not args.no_cache, ttl=args.cache_ttl)
    return RegistryClient(cache=cache, workers=args.jobs)


# ---------------------------------------------------------------- check


def cmd_check(args):
    minimum = _min_age(args.min_age)
    deps = []
    for text in args.specs:
        try:
            deps.append(parse_spec(text))
        except SpecError as exc:
            raise SystemExit(f"dep-age-gate: {exc}")
    results = _client(args).evaluate(deps, minimum)
    if args.json:
        report.render_json(results, minimum, [], [], sys.stdout)
    else:
        report.render_table(results)
        counts = report.summarize(results)
        print(
            f"\nminimum age {humanize(minimum)} "
            f"({minimum}s) - pass {counts['PASS']}, fail {counts['FAIL']}, "
            f"unknown {counts['UNKNOWN']}"
        )
    return 1 if any(r.failed for r in results) else 0


# ---------------------------------------------------------------- audit


def cmd_audit(args):
    minimum = _min_age(args.min_age)
    allow = _build_allow(args.allow, args.reason)

    if args.before and not args.lock:
        raise SystemExit("dep-age-gate: --before requires --lock")

    targets, errors = audit_mod.build_targets(
        paths=args.paths,
        base=args.base,
        lock=args.lock,
        before=args.before,
        scan_all=args.all,
        exclude=args.exclude,
    )
    deps, skips, parse_errors = audit_mod.collect(
        targets, allow_binary_lock=args.allow_binary_lock
    )
    errors = list(errors) + list(parse_errors)

    results = _client(args).evaluate(deps, minimum, allow=allow) if deps else []

    if args.json:
        report.render_json(results, minimum, skips, errors, sys.stdout)
    else:
        if not targets and not errors:
            print("dep-age-gate: no lockfile changes to audit")
        elif not deps and not errors:
            names = ", ".join(t.label for t in targets)
            print(f"dep-age-gate: no new dependency versions in {names}")
        else:
            report.render_table(results, show_pass=args.verbose)
            counts = report.summarize(results)
            print(
                f"\n{len(targets)} lockfile(s), {len(deps)} new or changed version(s); "
                f"minimum age {humanize(minimum)}. "
                f"pass {counts['PASS']}, fail {counts['FAIL']}, "
                f"unknown {counts['UNKNOWN']}, allowed {counts['ALLOWED']}"
            )
        if args.verbose and skips:
            print("\nnot checked (no registry publish date):")
            for skip in skips:
                print(f"  {skip.source}: {skip.what} - {skip.reason}")
        for message in errors:
            print(f"error: {message}", file=sys.stderr)

    failed = any(r.failed for r in results)
    if failed or errors:
        if os.environ.get(BYPASS_ENV) == "1":
            print(
                f"\ndep-age-gate: {BYPASS_ENV}=1 set - failing versions accepted anyway.",
                file=sys.stderr,
            )
            return 0
        return 1
    return 0


# ---------------------------------------------------------------- init-uv


def cmd_init_uv(args):
    exit_code = 0
    for project in args.projects:
        path = project
        if os.path.isdir(path):
            path = os.path.join(path, "pyproject.toml")
        if not os.path.exists(path):
            print(f"error: {path}: no pyproject.toml", file=sys.stderr)
            exit_code = 1
            continue
        with open(path, "r", encoding="utf-8") as handle:
            old = handle.read()
        new, action = uvinit.plan(old, args.span)
        if action == "noop":
            print(f"{path}: already sets exclude-newer = \"{args.span}\"")
            continue
        text = uvinit.diff(path, old, new)
        print(text, end="" if text.endswith("\n") else "\n")
        if args.write:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new)
            print(f"{path}: written ({action}). Run `uv lock` to re-resolve.")
        else:
            print(f"{path}: dry run ({action}); pass --write to apply.")
    return exit_code


# ---------------------------------------------------------------- parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dep-age-gate",
        description="Refuse dependency versions younger than a minimum age.",
    )
    parser.add_argument("--version", action="version", version=f"dep-age-gate {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def shared(target):
        target.add_argument("--min-age", default=DEFAULT_MIN_AGE,
                            help="minimum age, e.g. 72h, 3d, PT72H (default %(default)s)")
        target.add_argument("--json", action="store_true", help="machine-readable output")
        target.add_argument("--no-cache", action="store_true", help="ignore the on-disk cache")
        target.add_argument("--cache-ttl", type=int, default=24 * 3600,
                            help="cache lifetime in seconds (default %(default)s)")
        target.add_argument("--jobs", type=int, default=12, help="parallel registry lookups")

    check = subparsers.add_parser("check", help="check package specs directly")
    check.add_argument("specs", nargs="+",
                       metavar="SPEC",
                       help="npm:vitest@5.0.0 pypi:requests@2.32.0 crates:serde@1.0.200 "
                            "maven:org.apache.commons:commons-lang3:3.14.0")
    shared(check)
    check.set_defaults(func=cmd_check)

    audit = subparsers.add_parser("audit", help="audit lockfile changes")
    audit.add_argument("paths", nargs="*", help="lockfiles or directories (default: staged changes)")
    audit.add_argument("--base", help="git ref to compare against (CI: the PR base sha)")
    audit.add_argument("--lock", help="single lockfile to audit")
    audit.add_argument("--before", help="snapshot of --lock taken before the command ran")
    audit.add_argument("--all", action="store_true",
                       help="check every version in the file, not just new ones")
    audit.add_argument("--allow", action="append", default=[],
                       metavar="PKG@VER", help="accept one young version (repeatable)")
    audit.add_argument("--reason", help="required with --allow; recorded in the output")
    audit.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                       help="skip lockfiles matching this glob (repeatable). "
                            "Also read from .dep-age-gate-ignore in the repo root.")
    audit.add_argument("--allow-binary-lock", action="store_true",
                       help="skip bun.lockb instead of failing")
    audit.add_argument("-v", "--verbose", action="store_true",
                       help="also list passing versions and skipped entries")
    shared(audit)
    audit.set_defaults(func=cmd_audit)

    init_uv = subparsers.add_parser("init-uv", help="add uv's exclude-newer gate to a project")
    init_uv.add_argument("projects", nargs="+", help="project directory or pyproject.toml path")
    init_uv.add_argument("--span", default=uvinit.DEFAULT_SPAN,
                         help="relative span for exclude-newer (default %(default)s)")
    init_uv.add_argument("--write", action="store_true", help="apply the change")
    init_uv.set_defaults(func=cmd_init_uv)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
