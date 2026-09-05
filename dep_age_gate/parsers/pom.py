"""pom.xml — direct dependencies that carry an explicit version.

Versions written as `${property}` are resolved from <properties>, <version>
and <parent><version> when possible. Anything still unresolved is a skip, not
a silent pass.
"""

import re
import xml.etree.ElementTree as ET

from dep_age_gate.model import MAVEN, Dep, ParseOutcome, Skip

_NS = re.compile(r"^\{[^}]*\}")
_PROP = re.compile(r"\$\{([^}]+)\}")


def _tag(element):
    return _NS.sub("", element.tag)


def _child(element, name):
    for item in element:
        if _tag(item) == name:
            return item
    return None


def _text(element, name):
    found = _child(element, name)
    return (found.text or "").strip() if found is not None and found.text else None


def _collect_properties(root):
    values = {}
    properties = _child(root, "properties")
    if properties is not None:
        for item in properties:
            values[_tag(item)] = (item.text or "").strip()
    own_version = _text(root, "version")
    parent = _child(root, "parent")
    parent_version = _text(parent, "version") if parent is not None else None
    version = own_version or parent_version
    if version:
        values.setdefault("project.version", version)
        values.setdefault("version", version)
    group = _text(root, "groupId") or (_text(parent, "groupId") if parent is not None else None)
    if group:
        values.setdefault("project.groupId", group)
    return values


def _resolve(value, properties, depth=0):
    if value is None or depth > 5:
        return value
    match = _PROP.fullmatch(value.strip())
    if match:
        replacement = properties.get(match.group(1))
        if replacement is None:
            return value
        return _resolve(replacement, properties, depth + 1)
    if "${" in value:
        def swap(found):
            return properties.get(found.group(1), found.group(0))
        replaced = _PROP.sub(swap, value)
        return replaced if "${" not in replaced else value
    return value


def parse(text, source, include_dependency_management=True, **_):
    outcome = ParseOutcome()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        outcome.errors.append(f"{source}: not valid XML ({exc})")
        return outcome

    properties = _collect_properties(root)
    blocks = []
    direct = _child(root, "dependencies")
    if direct is not None:
        blocks.append(("dependencies", direct))
    if include_dependency_management:
        management = _child(root, "dependencyManagement")
        if management is not None:
            inner = _child(management, "dependencies")
            if inner is not None:
                blocks.append(("dependencyManagement", inner))

    if not blocks:
        outcome.skips.append(Skip(source, "pom.xml", "no <dependencies> block"))
        return outcome

    seen = set()
    for where, block in blocks:
        for dependency in block:
            if _tag(dependency) != "dependency":
                continue
            group = _resolve(_text(dependency, "groupId"), properties)
            artifact = _resolve(_text(dependency, "artifactId"), properties)
            version = _resolve(_text(dependency, "version"), properties)
            if not group or not artifact:
                continue
            label = f"{group}:{artifact}"
            if not version:
                outcome.skips.append(
                    Skip(source, label, f"no <version> in <{where}> (inherited from a BOM/parent)")
                )
                continue
            if "${" in version:
                outcome.skips.append(
                    Skip(source, f"{label}:{version}", "version property could not be resolved")
                )
                continue
            if (label, version) in seen:
                continue
            seen.add((label, version))
            outcome.deps.append(Dep(MAVEN, label, version, source, where))
    return outcome
