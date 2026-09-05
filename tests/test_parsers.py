"""One test per fixture: the exact dependency set, the skips, and no errors.

Everything is routed through dep_age_gate.parsers.parse(path, text, label).
`path` is only used to pick the parser, so the fixture files can keep
distinguishing names while the router sees the real lockfile basename.
"""

import pytest

from conftest import fixture_text
from dep_age_gate.parsers import is_supported, parse, parser_for


def run(fixture, path, label=None, **kwargs):
    return parse(path, fixture_text(fixture), label or path, **kwargs)


def keys(outcome):
    return {dep.key for dep in outcome.deps}


def skipped(outcome):
    return {skip.what for skip in outcome.skips}


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "pnpm-lock.yml",
        "bun.lock", "bun.lockb", "yarn.lock", "uv.lock", "poetry.lock", "Cargo.lock",
        "gradle.lockfile", "pom.xml", "requirements.txt", "requirements-dev.txt",
        "requirements.in", "/a/b/c/package-lock.json",
    ],
)
def test_router_recognises_every_supported_name(path):
    assert is_supported(path)
    assert parser_for(path) is not None


@pytest.mark.parametrize("path", ["Gemfile.lock", "composer.lock", "go.sum", "package.json"])
def test_router_rejects_unsupported_names(path):
    assert not is_supported(path)


def test_unrouted_path_is_an_error_not_an_empty_result():
    outcome = parse("Gemfile.lock", "irrelevant", "Gemfile.lock")
    assert outcome.deps == []
    assert outcome.errors and "no parser" in outcome.errors[0]


# ---------------------------------------------------------------- npm


def test_package_lock_v3():
    outcome = run("package-lock-v3.json", "package-lock.json")
    assert keys(outcome) == {
        ("npm", "@types/node", "22.5.0"),
        ("npm", "left-pad", "1.3.0"),
        ("npm", "lodash", "4.17.21"),
    }
    assert len(outcome.skips) == 3
    assert skipped(outcome) == {
        "tiny-git-dep@2.0.1",          # git+ssh resolved
        "node_modules/ui",             # "link": true
        "packages/ui",                 # the workspace member's own block
    }
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert reasons["node_modules/ui"] == "workspace link"
    assert reasons["packages/ui"] == "workspace member, not a registry install"
    assert reasons["tiny-git-dep@2.0.1"].startswith("non-registry source git+ssh://")
    assert outcome.errors == []


def test_package_lock_v1():
    outcome = run("package-lock-v1.json", "package-lock.json")
    assert keys(outcome) == {
        ("npm", "ansi-styles", "4.3.0"),
        ("npm", "color-convert", "2.0.1"),   # nested under ansi-styles
        ("npm", "chalk", "4.1.2"),
        ("npm", "semver", "7.6.3"),
    }
    assert len(outcome.skips) == 1
    skip = outcome.skips[0]
    assert skip.what.startswith("internal-tools@git+ssh://")
    assert skip.reason == "not a registry version"
    assert outcome.errors == []


def test_package_lock_v1_nested_detail_records_the_path():
    outcome = run("package-lock-v1.json", "package-lock.json")
    details = {dep.name: dep.detail for dep in outcome.deps}
    assert details["color-convert"] == "ansi-styles/color-convert"


# ---------------------------------------------------------------- pnpm


def test_pnpm_lock_v9():
    outcome = run("pnpm-lock-v9.yaml", "pnpm-lock.yaml")
    assert keys(outcome) == {
        ("npm", "@eslint/js", "9.0.0"),
        ("npm", "eslint", "9.0.0"),
        ("npm", "lodash", "4.17.21"),
        ("npm", "typescript", "5.4.5"),
    }
    assert len(outcome.skips) == 1
    skip = outcome.skips[0]
    assert skip.what.startswith("is-positive@https://codeload.github.com/")
    assert skip.reason == "not a registry version"
    assert outcome.errors == []


def test_pnpm_v9_snapshots_section_is_not_a_second_source_of_versions():
    # `snapshots:` repeats every package with its peer suffix
    # (eslint@9.0.0(typescript@5.4.5)); only `packages:` is read.
    text = fixture_text("pnpm-lock-v9.yaml")
    assert "eslint@9.0.0(typescript@5.4.5):" in text
    outcome = parse("pnpm-lock.yaml", text, "pnpm-lock.yaml")
    assert len(outcome.deps) == 4


def test_pnpm_lock_v6_strips_the_peer_suffix():
    outcome = run("pnpm-lock-v6.yaml", "pnpm-lock.yaml")
    assert keys(outcome) == {
        ("npm", "@eslint/js", "8.57.0"),
        ("npm", "eslint", "8.57.0"),       # key was /eslint@8.57.0(typescript@5.4.5)
        ("npm", "lodash", "4.17.21"),
        ("npm", "typescript", "5.4.5"),
    }
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what == "file:../shared-ui"
    assert outcome.skips[0].reason == "unparsable package key"
    assert outcome.errors == []


def test_pnpm_lock_v5_strips_the_peer_hash_suffix():
    outcome = run("pnpm-lock-v5.yaml", "pnpm-lock.yaml")
    assert keys(outcome) == {
        ("npm", "@eslint/js", "8.57.0"),
        ("npm", "eslint", "8.57.0"),
        ("npm", "lodash", "4.17.21"),
        ("npm", "react-dom", "18.3.1"),   # key was /react-dom/18.3.1_react@18.3.1
    }
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what.startswith("github.com/kevva/is-positive/")
    assert outcome.errors == []


# ---------------------------------------------------------------- bun


def test_bun_lock_with_trailing_commas():
    outcome = run("bun.lock", "bun.lock")
    assert keys(outcome) == {
        ("npm", "@types/node", "22.5.0"),
        ("npm", "left-pad", "1.3.0"),
        ("npm", "lodash", "4.17.21"),
        ("npm", "typescript", "5.4.5"),
    }
    assert len(outcome.skips) == 2
    assert skipped(outcome) == {
        "@my-app/ui@workspace:packages/ui",
        "tiny-git-dep@git+ssh://git@github.com/example/tiny-git-dep.git"
        "#8f0a2b1c9d3e4f5061728394a5b6c7d8e9f00112",
    }
    assert outcome.errors == []


# ---------------------------------------------------------------- yarn


def test_yarn_v1():
    outcome = run("yarn-v1.lock", "yarn.lock")
    assert keys(outcome) == {
        ("npm", "@types/node", "22.5.0"),
        ("npm", "left-pad", "1.3.0"),
        ("npm", "lodash", "4.17.21"),
        ("npm", "typescript", "5.4.5"),
    }
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what == "shared-ui@file:../shared-ui"
    assert outcome.skips[0].reason == "non-registry source file:../shared-ui"
    assert outcome.errors == []


def test_yarn_berry():
    outcome = run("yarn-berry.lock", "yarn.lock")
    assert keys(outcome) == {
        ("npm", "@types/node", "22.5.0"),
        ("npm", "left-pad", "1.3.0"),
        ("npm", "undici-types", "6.19.8"),
    }
    assert len(outcome.skips) == 2
    assert "my-app@workspace:." in skipped(outcome)
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert reasons["my-app@workspace:."] == "non-npm protocol workspace:"
    patched = [what for what in skipped(outcome) if what.startswith("lodash@patch:")]
    assert len(patched) == 1
    assert outcome.errors == []


# ---------------------------------------------------------------- python


def test_uv_lock():
    outcome = run("uv.lock", "uv.lock")
    assert keys(outcome) == {
        ("pypi", "certifi", "2024.8.30"),
        ("pypi", "requests", "2.32.3"),
        ("pypi", "urllib3", "2.2.3"),
    }
    assert len(outcome.skips) == 3
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert reasons["my-app==0.1.0"] == "source is editable, not a registry"
    assert reasons["internal-helpers==0.3.1"] == "source is git, not a registry"
    assert reasons["shared-schemas==1.2.0"] == "source is directory, not a registry"
    assert outcome.errors == []


def test_poetry_lock():
    outcome = run("poetry.lock", "poetry.lock")
    assert keys(outcome) == {
        ("pypi", "certifi", "2024.8.30"),
        ("pypi", "requests", "2.32.3"),
        ("pypi", "urllib3", "2.2.3"),
    }
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what == "internal-helpers==0.3.1"
    assert outcome.skips[0].reason == "[package.source] type = git"
    assert outcome.errors == []


def test_requirements_txt():
    outcome = run("requirements.txt", "requirements.txt")
    assert keys(outcome) == {
        ("pypi", "certifi", "2024.8.30"),   # written with --hash continuations
        ("pypi", "requests", "2.32.3"),
        ("pypi", "urllib3", "2.2.3"),       # written with an environment marker
        ("pypi", "Flask", "3.0.3"),         # written with an extras group
    }
    assert len(outcome.skips) == 2
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert reasons["httpx>=0.27.0"] == "not pinned with == ; no single version to date"
    url_skip = [w for w in reasons if w.startswith("internal-helpers @ https://")]
    assert len(url_skip) == 1
    assert reasons[url_skip[0]] == "direct URL requirement"
    assert outcome.errors == []


# ---------------------------------------------------------------- rust


def test_cargo_lock():
    outcome = run("Cargo.lock", "Cargo.lock")
    assert keys(outcome) == {
        ("crates", "itoa", "1.0.11"),
        ("crates", "serde", "1.0.210"),
        ("crates", "serde_json", "1.0.128"),
    }
    assert len(outcome.skips) == 2
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert reasons["my-app 0.1.0"] == "workspace/path member"      # no `source`
    assert reasons["internal-helpers 0.3.1"].startswith("non-crates.io source git+")
    assert outcome.errors == []


# ---------------------------------------------------------------- java


def test_gradle_lockfile():
    outcome = run("gradle.lockfile", "gradle.lockfile")
    assert keys(outcome) == {
        ("maven", "com.google.guava:guava", "33.0.0-jre"),
        ("maven", "com.squareup.okhttp3:okhttp", "4.12.0"),
        ("maven", "org.apache.commons:commons-lang3", "3.14.0"),
        ("maven", "org.slf4j:slf4j-api", "2.0.13"),
    }
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what == "com.example:internal-lib=compileClasspath"
    assert outcome.skips[0].reason == "not a group:artifact:version line"
    assert outcome.errors == []


def test_gradle_lockfile_records_the_configurations():
    outcome = run("gradle.lockfile", "gradle.lockfile")
    detail = {dep.name: dep.detail for dep in outcome.deps}
    assert detail["org.slf4j:slf4j-api"] == "compileClasspath,runtimeClasspath"


def test_pom_xml_resolves_properties_and_skips_the_unresolvable_one():
    outcome = run("pom.xml", "pom.xml")
    assert keys(outcome) == {
        ("maven", "com.google.guava:guava", "33.0.0-jre"),        # ${guava.version}
        ("maven", "org.slf4j:slf4j-api", "2.0.13"),               # ${slf4j.version}
        ("maven", "org.apache.commons:commons-lang3", "3.14.0"),  # literal
        ("maven", "com.example:my-app-core", "1.4.0"),            # ${project.version}
        ("maven", "org.junit:junit-bom", "5.10.2"),               # dependencyManagement
    }
    assert len(outcome.skips) == 2
    reasons = {skip.what: skip.reason for skip in outcome.skips}
    assert (
        reasons["com.fasterxml.jackson.core:jackson-databind:${jackson.version}"]
        == "version property could not be resolved"
    )
    assert reasons["org.junit.jupiter:junit-jupiter"].startswith("no <version>")
    assert outcome.errors == []


# ---------------------------------------------------------------- negative


def test_malformed_json_is_an_error():
    outcome = parse("package-lock.json", '{"packages": {', "package-lock.json")
    assert outcome.deps == []
    assert outcome.errors
    assert "not valid JSON" in outcome.errors[0]


def test_malformed_bun_lock_is_an_error():
    outcome = parse("bun.lock", '{"packages": [', "bun.lock")
    assert outcome.deps == []
    assert outcome.errors
    assert "not valid JSON/JSONC" in outcome.errors[0]


def test_malformed_pom_is_an_error():
    outcome = parse("pom.xml", "<project><dependencies>", "pom.xml")
    assert outcome.deps == []
    assert outcome.errors
    assert "not valid XML" in outcome.errors[0]


def test_package_lock_without_a_known_section_is_an_error():
    outcome = parse("package-lock.json", '{"name": "x"}', "package-lock.json")
    assert outcome.deps == []
    assert outcome.errors
    assert "unrecognised package-lock format" in outcome.errors[0]


def test_empty_toml_lockfiles_are_errors_not_clean_results():
    for path in ("uv.lock", "poetry.lock", "Cargo.lock"):
        outcome = parse(path, "# nothing here\n", path)
        assert outcome.deps == []
        assert outcome.errors == [f"{path}: no [[package]] blocks found"]


def test_bun_lockb_is_an_error_by_default():
    outcome = parse("bun.lockb", b"\x00\x01binary", "bun.lockb")
    assert outcome.deps == []
    assert outcome.skips == []
    assert len(outcome.errors) == 1
    assert "binary lockfile" in outcome.errors[0]
    assert "--allow-binary-lock" in outcome.errors[0]


def test_bun_lockb_is_a_skip_with_allow_binary_lock():
    outcome = parse(
        "bun.lockb", b"\x00\x01binary", "bun.lockb", allow_binary_lock=True
    )
    assert outcome.deps == []
    assert outcome.errors == []
    assert len(outcome.skips) == 1
    assert outcome.skips[0].what == "bun.lockb"
    assert "--allow-binary-lock" in outcome.skips[0].reason


def test_npm_lock_nested_node_modules_uses_innermost_name():
    import json

    from dep_age_gate.parsers import npm_lock

    lock = json.dumps(
        {
            "name": "app",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "app", "version": "1.0.0"},
                "node_modules/@tailwindcss/oxide-wasm32-wasi": {
                    "version": "4.1.0",
                    "resolved": "https://registry.npmjs.org/@tailwindcss/oxide-wasm32-wasi/-/oxide-wasm32-wasi-4.1.0.tgz",
                },
                "node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/tslib": {
                    "version": "2.8.1",
                    "resolved": "https://registry.npmjs.org/tslib/-/tslib-2.8.1.tgz",
                },
                "node_modules/@tailwindcss/oxide-wasm32-wasi/node_modules/@emnapi/core": {
                    "version": "1.7.1",
                    "resolved": "https://registry.npmjs.org/@emnapi/core/-/core-1.7.1.tgz",
                },
            },
        }
    )
    outcome = npm_lock.parse(lock, "package-lock.json")
    names = sorted((d.name, d.version) for d in outcome.deps)
    assert names == [
        ("@emnapi/core", "1.7.1"),
        ("@tailwindcss/oxide-wasm32-wasi", "4.1.0"),
        ("tslib", "2.8.1"),
    ]
