"""The API reference: one markdown page for each public name in `fastatp.__all__`.

griffe reads the signatures and docstrings from the source, without importing
the package. The pages are markdown like every other page on the site, so
build.py renders them, links them and serves their markdown copies the same
way. Nothing here is written to the repo.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import griffe

# Where the generated pages pretend to live, so their links to each other
# and to the source resolve like any other page's. No such folder exists.
FOLDER = "website/api"


@dataclass
class ApiPage:
    name: str
    src: str  # the pretend path, FOLDER/<name>.md
    markdown: str
    summary: str
    origin: str  # the source file, repo-relative, with its line: fastatp/peer.py#L12


def public_names(root: Path) -> list[str]:
    """`__all__`, read from the source the way griffe reads everything else."""
    tree = ast.parse((root / "fastatp/__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return [str(name) for name in ast.literal_eval(node.value)]
    raise ValueError("fastatp/__init__.py has no __all__")


ROLE = re.compile(r":(?:class|meth|func|attr|mod|exc|data|obj):`~?([^`]+)`")
LITERAL = re.compile(r"``([^`]+)``")
CODE = re.compile(r"`([A-Za-z_][\w.]*)(\(\))?`")


class Docs:
    """Turns the package's docstrings into markdown that links to its own pages."""

    def __init__(self, members: dict[str, set[str]]) -> None:
        self.members = members  # public name -> the member names its page documents

    def text(self, obj: Any, here: str) -> str:
        if not obj.docstring:
            return ""
        out = []
        for line in obj.docstring.value.splitlines():
            if line.startswith("    "):  # an indented example stays as written
                out.append(line)
                continue
            line = ROLE.sub(r"`\1`", line)
            line = LITERAL.sub(r"`\1`", line)
            if line.endswith("::"):
                line = line[:-1]
            out.append(CODE.sub(lambda m: self.link(m, here), line))
        return "\n".join(out).strip()

    def summary(self, obj: Any) -> str:
        if not obj.docstring:
            return ""
        first = obj.docstring.value.split("\n\n")[0]
        first = LITERAL.sub(r"\1", ROLE.sub(r"\1", first))
        return " ".join(first.split())

    def link(self, match: re.Match[str], here: str) -> str:
        """`Peer` or `Peer.verify` becomes a link when the site has that page."""
        dotted = match.group(1)
        name, _, member = dotted.partition(".")
        if name not in self.members or (name == here and not member):
            return match.group(0)
        anchor = f"#{member}" if member in self.members[name] else ""
        if member and not anchor:
            return match.group(0)
        target = anchor if name == here else f"{name}.md{anchor}"
        return f"[`{dotted}{match.group(2) or ''}`]({target})"


def split_args(text: str) -> list[str]:
    """Split a parameter list on its top-level commas."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    last = text[start:].strip()
    return [*parts, last] if last else parts


def signature(func: Any, name: str, drop_self: bool) -> str:
    """One line when it fits, one parameter per line when it does not."""
    full = str(func.signature(name=name, return_type=name == func.name))
    head, _, rest = full.partition("(")
    depth, end = 1, 0
    for end, ch in enumerate(rest):  # noqa: B007
        depth += (ch in "([{") - (ch in ")]}")
        if depth == 0:
            break
    args, tail = split_args(rest[:end]), rest[end + 1 :]
    if drop_self and args and args[0] in ("self", "cls"):
        args = args[1:]
    prefix = "async " if "async" in func.labels else ""
    line = f"{prefix}{head}({', '.join(args)}){tail}"
    if len(line) <= 88:
        return line
    body = "".join(f"    {arg},\n" for arg in args)
    return f"{prefix}{head}(\n{body}){tail}"


def own_members(cls: Any) -> tuple[list[Any], list[Any]]:
    """A class's own public attributes and methods, in source order."""
    attrs, funcs = [], []
    for name, member in cls.members.items():
        if name.startswith("_") or member.is_alias:
            continue
        if member.kind.value == "attribute" or "property" in member.labels:
            attrs.append(member)
        elif member.kind.value == "function":
            funcs.append(member)
    return attrs, funcs


def code(text: str) -> list[str]:
    return ["```python", text, "```", ""]


def attribute_line(attr: Any, docs: Docs, here: str) -> str:
    kind = getattr(attr, "annotation", None) or getattr(attr, "returns", None)
    typed = f": {kind}" if kind else ""
    text = docs.text(attr, here).replace("\n", " ")
    return f"- `{attr.name}{typed}`" + (f": {text}" if text else "")


def class_page(cls: Any, docs: Docs) -> list[str]:
    lines = []
    init = cls.members.get("__init__")
    if init is not None and not init.is_alias:
        lines += code(signature(init, cls.name, drop_self=True))
    bases = [str(b) for b in cls.bases]
    if bases:
        linked = ", ".join(f"[`{b}`]({b}.md)" if b in docs.members else f"`{b}`" for b in bases)
        lines += [f"Based on {linked}.", ""]
    lines += [docs.text(cls, cls.name), ""]
    attrs, funcs = own_members(cls)
    if attrs:
        lines += ["## Attributes", ""]
        lines += [attribute_line(a, docs, cls.name) for a in attrs]
        lines += [""]
    if funcs:
        lines += ["## Methods", ""]
        for func in funcs:
            lines += [f"### `{func.name}`", ""]
            lines += code(signature(func, func.name, drop_self=True))
            lines += [docs.text(func, cls.name), ""]
    return lines


def module_page(module: Any, docs: Docs) -> list[str]:
    lines = [docs.text(module, module.name), ""]
    listed = [n for n in module.members if n in docs.members and n != module.name]
    if listed:
        lines += ["## In this module", ""]
        lines += [f"- [`{n}`]({n}.md)" for n in listed]
        lines += [""]
    return lines


def attribute_page(attr: Any, docs: Docs) -> list[str]:
    typed = f": {attr.annotation}" if attr.annotation else ""
    value = f" = {attr.value}" if attr.value is not None else ""
    return [*code(f"{attr.name}{typed}{value}"), docs.text(attr, attr.name), ""]


def pages(root: Path) -> tuple[list[ApiPage], str]:
    """Every public name's page, and the index page that lists them by module."""
    package = griffe.load("fastatp", search_paths=[str(root)])
    names = public_names(root)
    targets = {}
    for name in names:
        obj = package[name]
        targets[name] = obj.final_target if obj.is_alias else obj

    members: dict[str, set[str]] = {}
    for name, obj in targets.items():
        members[name] = set()
        if obj.kind.value == "class":
            _, funcs = own_members(obj)
            members[name] = {f.name for f in funcs}
    docs = Docs(members)

    built: list[ApiPage] = []
    by_module: dict[str, list[ApiPage]] = {}
    for name, obj in targets.items():
        kind = obj.kind.value
        module = obj.path if kind == "module" else obj.module.path
        file = obj.filepath.relative_to(root).as_posix()
        origin = file if kind == "module" else f"{file}#L{obj.lineno}"
        up = "../" * FOLDER.count("/") + "../"
        article = "An" if kind == "attribute" else "A"
        lines = [f"# `{name}`", "", f"{article} {kind} in `{module}` ([source]({up}{origin})).", ""]
        if kind == "class":
            lines += class_page(obj, docs)
        elif kind == "function":
            lines += code(signature(obj, name, drop_self=False))
            lines += [docs.text(obj, name), ""]
        elif kind == "module":
            lines += module_page(obj, docs)
        else:
            lines += attribute_page(obj, docs)
        markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
        page = ApiPage(name, f"{FOLDER}/{name}.md", markdown, docs.summary(obj), origin)
        built.append(page)
        by_module.setdefault(module, []).append(page)

    index = ["# API reference", ""]
    index += [
        f"The {len(names)} names `fastatp` exports, grouped by the module that",
        "defines them. Import each one from the top: `from fastatp import Peer`.",
        "",
    ]
    for module in sorted(by_module):
        index += [f"## `{module}`", ""]
        for page in by_module[module]:
            tail = f": {page.summary}" if page.summary else ""
            index.append(f"- [`{page.name}`]({page.name}.md){tail}")
        index.append("")
    return built, "\n".join(index)
