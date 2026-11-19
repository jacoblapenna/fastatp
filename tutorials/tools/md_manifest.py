"""Turn a tutorial's markdown into the score the conductor plays.

python md_manifest.py GETTING_STARTED.md > GETTING_STARTED.json

The markdown is the score. The conductor cues each step to the machine that
plays it. Most steps come straight from what the guide already says: a ```bash
block whose lines read `./lab.sh <machine> <command>` is that machine's line,
a ``### `name.py` `` heading carries the file's code (the section's first
```python block) so the screen can show it while the prose talks about it,
a `> 🔑 **Key concept: ...**` blockquote (or `⚠️ **Important: ...**`, or
`💡 **Did you know: ...**`) is a pause with a "Got it", and each
paragraph of prose is a caption: one press of Next each, flushed before the
next command, heading or stop so no paragraph is lost. Bulleted and numbered
lists keep their lines. Everything else is a cue in an HTML comment, which
GitHub and fastatp.com do not render:

  <!-- @demo machines alice bob eve -->   who is in the cast
  <!-- @demo start -->  <!-- @demo end --> what the screen plays (any number of ranges)
  <!-- @demo run bob: <command> -->       a line the prose describes but no block shows;
                                          directly before a bash block it replaces the block
  <!-- @demo skip -->                     the next bash block is for a person, not the screen
  <!-- @demo save rkey <literal> [from N] -->
                                          the Nth command of the last block returned a uri;
                                          later commands get its rkey wherever <literal> appears
  <!-- @demo stop bob -->                 Ctrl-C whatever bob left running
  <!-- @demo wait 5 -->                   let time pass
  <!-- @demo expand eve -->  collapse  focus
                                          frame sizes; focus expands these, collapses the rest
  <!-- @demo open wire -->  close         keep a folded section (alice.repo, bob.tunnel, wire)
                                          open from here on, or fold it again
  <!-- @demo point alice.tunnel diagram --> right after a key concept or a paragraph: what it
                                          points at, outlined and opened while it is on
  <!-- @demo terminal -->...<!-- @demo /terminal -->
                                          prose for the person at a terminal ("Ctrl-C it",
                                          "in a second terminal"), skipped on the screen,
                                          where it is a press of Next; inside a paragraph,
                                          or on a line of its own to skip the next paragraph

Standard library only, so it runs anywhere the guide does.
"""

import json
import re
import sys

LAB = re.compile(r"^\./lab\.sh (\S+)(?: (.*))?$")
CAPTURE = re.compile(r"^(\w+)=\$\((.*)\)$")
FILE_HEADING = re.compile(r"^### `([\w.]+)`")  # a section that shows one lab file
CONCEPT = re.compile(
    r"^(🔑|⚠️|💡) \*\*(?:Key concept|Important|Did you know): (.+?)\*\*\s*(.*)$", re.S
)
STYLES = {"🔑": "key", "⚠️": "important", "💡": "tip"}
DIRECTIVE = re.compile(
    r"^<!-- @demo (/?\w+)(?:\s+([^<>]*?))? -->$"
)  # one comment, nothing else on the line
FULL_COMMENT = re.compile(
    r"^<!--[^<>]*-->$"
)  # a terminal span on a line of its own is prose, not this
TERMINAL_OPEN, TERMINAL_CLOSE = "<!-- @demo terminal -->", "<!-- @demo /terminal -->"
ITEM = re.compile(r"^(- |\d+\. )")  # a list item starts a line of its own in a caption


def as_lines(lines: list[str]) -> str:
    """Wrapped markdown lines as one text, each list item on a line of its own.

    As in markdown, a numbered list opens with ``1.``; a wrapped line that
    happens to begin with another number is prose."""
    out, in_list = "", False
    for line in lines:
        item = ITEM.match(line) is not None and (
            line.startswith("- ") or in_list or line.startswith("1. ")
        )
        out += ("\n" if item else " ") + line
        in_list = in_list or item
    return out.strip()


def without_terminal(text: str, inside: bool) -> tuple[str, bool]:
    """``text`` with its terminal spans removed, and whether a span is still
    open at the end (a span may run across paragraphs)."""
    kept: list[str] = []
    while text:
        if inside:
            j = text.find(TERMINAL_CLOSE)
            if j == -1:
                break
            text, inside = text[j + len(TERMINAL_CLOSE) :], False
        else:
            i = text.find(TERMINAL_OPEN)
            if i == -1:
                kept.append(text)
                break
            kept.append(text[:i])
            text, inside = text[i + len(TERMINAL_OPEN) :], True
    text = "".join(kept)  # the pieces keep their own spacing
    return "\n".join(" ".join(line.split()) for line in text.split("\n")).strip(), inside


LONG_RUNNING = ("follow.py", "tail.py")  # these stay up until Ctrl-C, or until they give up


def parse(text: str) -> dict:
    machines: list[str] = []
    steps: list[dict] = []
    active = False
    say: list[str] = []  # prose since the last step: a caption per paragraph
    replace_next_bash = False  # run directives just stood in for the next block
    skip_next_bash = False
    skip_next_para = False  # a terminal directive on its own line: the next paragraph is typing
    in_terminal = False  # inside a terminal span that started in an earlier paragraph
    last_block: list[dict] = []  # the run steps of the last bash block, for save
    file_heading: dict | None = None  # a `### `name.py`` heading waiting for its code

    def emit(step: dict) -> dict:
        step["id"] = len(steps)
        steps.append(step)
        return step

    def emit_say() -> None:
        for text in say:
            emit({"kind": "say", "text": text})
        say.clear()

    def command(line: str) -> dict | None:
        """One shell line from the guide, as a step, or None for a host-only line."""
        capture = None
        if m := CAPTURE.match(line):
            capture, line = m.group(1), m.group(2)
        m = LAB.match(line)
        if m is None:
            return None  # cd lab, echo "$CH", ./lab.sh up: the person's own shell, not a machine's
        who, rest = m.group(1), m.group(2) or ""
        if who == "logs" and rest in machines:
            return {
                "kind": "run",
                "machine": rest,
                "cmd": "cat /tmp/peer.log",
                "display": line,
                "bg": False,
            }
        if who == "restart" and rest in machines:
            return {"kind": "restart", "machine": rest, "display": line}
        if who not in machines:
            return None
        step = {
            "kind": "run",
            "machine": who,
            "cmd": rest,
            "display": line,
            "bg": any(s in rest for s in LONG_RUNNING),
        }
        if capture:
            step["capture"] = capture
        return step

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if m := DIRECTIVE.match(stripped):
            verb, arg = m.group(1), (m.group(2) or "").strip()
            i += 1
            if verb == "machines":
                machines = arg.split()
            elif verb == "start":
                active = True
            elif verb == "end":
                emit_say()
                active = False
            elif not active:
                continue
            elif verb == "run":
                who, _, cmd = arg.partition(":")
                emit_say()
                emit(
                    {
                        "kind": "run",
                        "machine": who.strip(),
                        "cmd": cmd.strip(),
                        "display": f"./lab.sh {who.strip()} {cmd.strip()}",
                        "bg": any(s in cmd for s in LONG_RUNNING),
                    }
                )
                replace_next_bash = True
            elif verb == "skip":
                skip_next_bash = True
            elif verb == "terminal":
                skip_next_para = True
            elif verb == "/terminal":
                pass  # the close of a span whose open was inline; nothing to do
            elif verb == "save":
                parts = arg.split()
                field, literal = parts[0], parts[1]
                n = int(parts[3]) if len(parts) > 3 and parts[2] == "from" else len(last_block)
                last_block[n - 1].setdefault("save", []).append(
                    {"field": field, "literal": literal}
                )
            elif verb == "stop":
                emit_say()
                emit({"kind": "stop", "machine": arg})
            elif verb == "wait":
                emit_say()
                emit({"kind": "wait", "seconds": float(arg)})
            elif verb in ("expand", "collapse", "focus"):
                emit({"kind": "layout", "action": verb, "machines": arg.split()})
            elif verb in ("open", "close"):
                emit({"kind": "layout", "action": verb, "targets": arg.split()})
            elif verb == "point":
                emit_say()  # the paragraph just above is the step that points
                if steps and steps[-1]["kind"] in ("concept", "say"):
                    steps[-1]["point"] = arg.split()
            else:
                raise SystemExit(f"line {i}: unknown @demo directive {verb!r}")
            continue

        if stripped.startswith("```"):
            info = stripped[3:].strip().split()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            if not active:
                continue
            lang = info[0] if info else ""
            if lang == "python" and file_heading is not None:
                file_heading["code"] = {"name": file_heading["file"], "text": "\n".join(body)}
                file_heading = None
            if lang == "bash":
                if skip_next_bash or replace_next_bash:
                    skip_next_bash = replace_next_bash = False
                    continue
                joined, buf = [], ""
                for raw in body:  # a trailing backslash continues the command on the next line
                    if raw.rstrip().endswith("\\"):
                        buf += raw.rstrip()[:-1] + " "
                    else:
                        joined.append((buf + raw).strip())
                        buf = ""
                block = [
                    s for s in (command(c) for c in joined if c and not c.startswith("#")) if s
                ]
                if block:
                    emit_say()
                    for s in block:
                        emit(s)
                    last_block = [s for s in block if s["kind"] == "run"]
            elif lang in ("text", "json") and last_block and steps and steps[-1] is last_block[-1]:
                last_block[-1]["expect"] = "\n".join(body)  # what the guide says should come back
            replace_next_bash = False
            continue

        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            if active and (m := CONCEPT.match(as_lines([q for q in quote if q]))):
                emit_say()
                emit(
                    {
                        "kind": "concept",
                        "style": STYLES[m.group(1)],
                        "title": m.group(2).rstrip("."),
                        "body": m.group(3),
                        "point": [],
                    }
                )
            replace_next_bash = False
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            i += 1
            if active:
                emit_say()  # what the section said last is still a caption
                heading = emit(
                    {"kind": "heading", "level": level, "text": stripped.lstrip("#").strip()}
                )
                file_heading = None
                if m := FILE_HEADING.match(stripped):
                    heading["file"] = m.group(1)
                    file_heading = heading  # its first python block is the file
            replace_next_bash = False
            continue

        if stripped and not stripped.startswith("|"):
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(r"^(#|>|```|\|)", lines[i].strip())
                and not FULL_COMMENT.match(lines[i].strip())
            ):
                para.append(lines[i].strip())
                i += 1
            if not para:  # a bare comment that is no directive: step over it
                i += 1
                continue
            # typing prose out, spacing tidied: what is left is the caption
            text, in_terminal = without_terminal(as_lines(para), in_terminal)
            if active and text and not skip_next_para:
                say.append(text)
            skip_next_para = False
            replace_next_bash = False
            continue
        i += 1

    return {"machines": machines, "steps": steps}


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        manifest = parse(f.read())
    manifest["source"] = sys.argv[1]
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=1)
