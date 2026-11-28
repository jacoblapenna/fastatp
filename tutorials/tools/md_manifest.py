"""Turn a tutorial's markdown into the score the conductor plays.

python md_manifest.py GETTING_STARTED.md > GETTING_STARTED.json

The markdown is the score. The conductor cues each step to the machine that
plays it. Most steps come straight from what the guide already says: a ```bash
block whose lines read `./lab.sh <machine> <command>` is that machine's line,
every ``### `name.py` `` section (its first paragraph and its first ```python
block) is a lab file the screen knows, shown beside the machine whose command
runs it, open at the part the command reaches, and every image is a picture the
screen can show,
a `> 🔑 **Key concept: ...**` blockquote (or `⚠️ **Important: ...**`, or
`💡 **Did you know: ...**`) is a pause with a "Got it", and each
paragraph of prose is a caption: one press of Next each, flushed before the
next command, heading or stop so no paragraph is lost. Bulleted and numbered
lists keep their lines. Everything else is a cue in an HTML comment, which
GitHub and fastatp.com do not render:

  <!-- @demo machines alice bob eve -->   who is in the cast: the machines that boot, and
                                          no others (`./lab.sh conductor` reads it)
  <!-- @demo peer orchestra.py -->        the program the machines run in place of peer.py
  <!-- @demo app geese /app/ -->          a machine's own web app, shown on the page as the
                                          browser in front of you (and, opened with
                                          `open phone`, as a second device's)
  <!-- @demo peer bank bank.py -->        the program one machine runs; it wins over the
                                          one for all (`./lab.sh conductor` reads both)
  <!-- @demo start -->  <!-- @demo end --> what the screen plays (any number of ranges)
  <!-- @demo chapter 3 "The private side" -->
  <!-- @demo blurb Eve reads the public repo like anyone... -->
                                          a sentence or two on the chapter, for its card
                                          the steps from here belong to chapter 3 (a number
                                          cued again continues its chapter); the show plays
                                          chapters in number order, whatever order the page
                                          tells them in, and the menu lists them
  <!-- @demo run bob: <command> -->       a line the prose describes but no block shows;
                                          directly before a bash block it replaces the block
  <!-- @demo skip -->                     the next bash block is for a person, not the screen
  <!-- @demo save rkey <literal> [from N] -->
                                          the Nth command of the last block returned a uri;
                                          later commands get its rkey wherever <literal> appears
  <!-- @demo glossary --> ... <!-- @demo /glossary -->
                                          a list of `- **Word**: one line` the show never
                                          says: with the words the key concepts list the
                                          same way, it is the glossary the page marks in
                                          captions and shows on hover
  <!-- @demo fails 2 3 -->                the 2nd and 3rd commands of the next block are meant
                                          to fail (a refusal, a ping nobody answers); with no
                                          numbers, every command in it. Every other command is
                                          meant to succeed, and the headless play checks both
  <!-- @demo prints bob -->               the next listing is what bob's terminal goes on to
                                          print, from the command bob left running, and not
                                          what the block above it printed. A listing is
                                          otherwise the output of its block's commands, and
                                          the headless play holds the machines to it
  <!-- @demo commit alice bob -->  or  <!-- @demo commit at self -->
                                          the conductor signs the record in the next ```json
                                          block into its own repo, for these members, and
                                          waits for each one's answer: a record in that
                                          member's own repo that names the commit's at://
                                          URI. With no members it is for nobody in
                                          particular (a setting every member reads) and
                                          waits for nothing. `at <rkey>` writes it at that
                                          key, so committing there again updates it in
                                          place; it is only for such a setting, and takes
                                          no members, since a key signed again must have
                                          no answer still owed to it. A fails cue above it names, by position,
                                          the members meant to stay silent. A ```json list
                                          of records is a sequence: the conductor signs one,
                                          waits for the named members it mentions to answer
                                          and for what they played to end, then the next
  <!-- @demo stop bob -->                 Ctrl-C whatever bob left running
  <!-- @demo wait 5 -->                   let time pass
  <!-- @demo expand eve -->  collapse  focus
                                          frame sizes; focus expands these, collapses the rest
  <!-- @demo open wire -->  close         keep a folded section (alice.repo, bob.tunnel, wire)
                                          open from here on, or fold it again; `close code`
                                          puts away the file a command showed
  <!-- @demo you app POST /signup -->     right under the paragraph that tells the viewer
                                          what to do in the app: that paragraph is their
                                          turn. The show waits, with Next off, until the
                                          app sends this request through the page (a path
                                          on the app's machine; it starts with the one
                                          given), answered with a 2xx or with the status
                                          given after it (... getRepo 404). `app` is this
                                          browser, `phone` the second device: it is what
                                          the turn points at
  <!-- @demo do app click #signup -->     under a `you` cue: what the headless play does
                                          for the viewer, in order. click <selector>,
                                          tick <selector> (a checkbox, left ticked),
                                          copy <selector> <device> <selector> (fill an
                                          input with what another element says), reload
  <!-- @demo point alice.tunnel diagram --> right after a key concept or a paragraph: what it
                                          points at, outlined and opened while it is on
  ![what it shows](sketches/private-repo.svg)
                                          an image goes with the paragraph or key concept
                                          above it; a concept with none takes the picture of
                                          the caption just above it (one paragraph may sit
                                          between); the conductor keeps every picture as a blob
  <!-- @demo ask Will eve's ping get an answer? ... -->
                                          a question before a command, for the viewer to
                                          answer in their head; the next press shows
  <!-- @demo code peer.py "async def commit(" -->
                                          the next block's commands (or the next turn's
                                          request) reach this file, here;
                                          a command that runs `python x.py` says so itself
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
IMAGE = re.compile(r"^!\[(.*?)\]\((\S+)\)$")  # a picture, on a line of its own


def code_for(cmd: str) -> dict | None:
    """The lab file a command names, when it runs a script by name."""
    if m := re.search(r"python (\w+\.py)", cmd):
        return {"file": m.group(1), "focus": ""}
    return None


CONCEPT = re.compile(
    r"^(🔑|⚠️|💡) \*\*(?:Key concept|Important|Did you know): (.+?)\*\*\s*(.*)$", re.S
)
STYLES = {"🔑": "key", "⚠️": "important", "💡": "tip"}
PROGRAM = re.compile(r"^[a-z0-9_]+\.py$")  # a lab program's file name
DIRECTIVE = re.compile(
    r"^<!-- @demo (/?\w+)(?:\s+([^<>]*?))? -->$"
)  # one comment, nothing else on the line
FULL_COMMENT = re.compile(
    r"^<!--[^<>]*-->$"
)  # a terminal span on a line of its own is prose, not this
TERMINAL_OPEN, TERMINAL_CLOSE = "<!-- @demo terminal -->", "<!-- @demo /terminal -->"
ITEM = re.compile(r"^(- |\d+\. )")  # a list item starts a line of its own in a caption
TERM = re.compile(r"^- \*\*(.+?)\*\*: (.+)$")  # a word a key concept defines, one line each


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


VISIBLE = (
    "run",
    "restart",
    "stop",
    "commit",
    "say",
    "concept",
    "chapter",
    "ask",
    "you",
)  # one press of Next each (a "you" step's press is the viewer's own, in the app)
DEVICES = ("app", "phone")  # this browser, and the second device
ACTIONS = {"click": 1, "tick": 1, "copy": 3, "reload": 0}  # what a `do` cue can do, and its words
LONG_RUNNING = ("follow.py", "tail.py")  # these stay up until Ctrl-C, or until they give up


def parse(text: str) -> dict:
    machines: list[str] = []
    peer = "peer.py"  # the program the machines run
    peers: dict[str, str] = {}  # a machine's own program, where the guide gives one
    peer_lines: dict[str, int] = {}  # where each was given, for the error that names it
    app: dict | None = None  # a machine's own web app, embedded on the page
    steps: list[dict] = []
    active = False
    say: list[dict] = []  # prose since the last step: a caption per paragraph
    glossary: list[dict] = []  # the words the key concepts define, in the guide's order
    recent_image: list | None = None  # [path, paragraphs since]: a picture a concept may inherit
    replace_next_bash = False  # run directives just stood in for the next block
    skip_next_bash = False
    skip_next_para = False  # a terminal directive on its own line: the next paragraph is typing
    in_terminal = False  # inside a terminal span that started in an earlier paragraph
    last_block: list[dict] = []  # the run steps of the last bash block, for save
    files: dict[str, dict] = {}  # name -> {"gloss", "text"}: the lab files the guide shows
    images: dict[str, dict] = {}  # path -> {"alt"}: the pictures the guide shows
    file_section: dict | None = None  # the `### `name.py`` section being read
    code_cue: dict | None = None  # a code cue waiting for the next bash block
    fails_cue: tuple[int, list[int]] | None = None  # its line, and which commands are meant to fail
    prints_cue: tuple[int, str] | None = None  # its line, and whose terminal the next listing is
    commit_cue: tuple[int, list[str], str] | None = None  # its line, its members, its rkey

    def emit(step: dict) -> dict:
        step["id"] = len(steps)
        steps.append(step)
        return step

    def emit_say() -> None:
        for caption in say:
            emit({"kind": "say", **caption})
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
        if code := code_for(rest):
            step["code"] = code
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
            elif verb == "app":
                words = arg.split()
                if len(words) != 2 or not words[1].startswith("/"):
                    raise SystemExit(
                        f"line {i}: @demo app names a machine and a path (geese /app/)"
                    )
                app = {"machine": words[0], "path": words[1], "line": i}
            elif verb == "peer":
                words = arg.split()
                if len(words) not in (1, 2) or not PROGRAM.match(words[-1]):
                    raise SystemExit(
                        f"line {i}: @demo peer names a program (orchestra.py), or a machine"
                        " and its program (bank bank.py)"
                    )
                if len(words) == 1:
                    peer = words[0]
                else:
                    peers[words[0]] = words[1]
                    peer_lines[words[0]] = i
            elif verb == "start":
                active = True
            elif verb == "end":
                emit_say()
                active = False
            elif verb == "glossary":
                # words defined for hovering only: a list the page reads and the show
                # never says. Everything up to the closing cue is the list.
                listed = []
                while i < len(lines) and lines[i].strip() != "<!-- @demo /glossary -->":
                    listed.append(lines[i].strip())
                    i += 1
                i += 1
                for entry in as_lines([x for x in listed if x]).split("\n"):
                    if term := TERM.match(entry):
                        glossary.append({"word": term.group(1), "text": term.group(2)})
            elif not active:
                continue
            elif verb == "run":
                who, _, cmd = arg.partition(":")
                emit_say()
                step = {
                    "kind": "run",
                    "machine": who.strip(),
                    "cmd": cmd.strip(),
                    "display": f"./lab.sh {who.strip()} {cmd.strip()}",
                    "bg": any(s in cmd for s in LONG_RUNNING),
                }
                if code := code_for(cmd):  # a script it runs opens beside the machine
                    step["code"] = code
                emit(step)
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
            elif verb == "prints":
                prints_cue = (i, arg)
            elif verb == "fails":
                fails_cue = (i, [int(n) for n in arg.split()])
            elif verb == "commit":
                words = arg.split()
                rkey = ""
                if "at" in words:
                    at = words.index("at")
                    if at + 2 != len(words):
                        raise SystemExit(f"line {i}: @demo commit ... at <rkey> ends the cue")
                    words, rkey = words[:at], words[at + 1]
                    if words:
                        # a key reused while members still owe an answer to the old
                        # record would take their late answer as one to the new
                        raise SystemExit(
                            f"line {i}: @demo commit ... at <rkey> is a setting for nobody in"
                            " particular, so it names no members"
                        )
                if strangers := [n for n in words if n not in machines]:
                    raise SystemExit(
                        f"line {i}: @demo commit names its members from the cast, not {strangers}"
                    )
                emit_say()
                commit_cue = (i, words, rkey)
            elif verb == "stop":
                emit_say()
                emit({"kind": "stop", "machine": arg})
            elif verb == "wait":
                emit_say()
                emit({"kind": "wait", "seconds": float(arg)})
            elif verb in ("expand", "collapse", "focus"):
                emit({"kind": "layout", "action": verb, "machines": arg.split()})
            elif verb in ("open", "close"):
                emit_say()  # where the cue sits is when it acts: after the paragraph above it
                emit({"kind": "layout", "action": verb, "targets": arg.split()})
            elif verb == "chapter":
                number, _, title = arg.partition(" ")
                emit_say()
                emit({"kind": "chapter", "number": int(number), "title": title.strip().strip('"')})
            elif verb == "ask":
                emit_say()
                emit({"kind": "ask", "text": arg})
            elif verb == "code":
                name, _, focus = arg.partition(" ")
                code_cue = {"file": name, "focus": focus.strip().strip('"')}
            elif verb == "blurb":
                if steps and steps[-1]["kind"] == "chapter":
                    steps[-1]["blurb"] = arg
            elif verb == "you":
                words = arg.split()
                if app is None:
                    raise SystemExit(f"line {i}: @demo you needs an app (@demo app ...) above it")
                if (
                    len(words) not in (3, 4)
                    or words[0] not in DEVICES
                    or words[1] not in ("GET", "POST")
                    or not words[2].startswith("/")
                    or (len(words) == 4 and not words[3].isdigit())
                ):
                    raise SystemExit(
                        f"line {i}: @demo you names a device ({' or '.join(DEVICES)}), and the"
                        " request that says it is done (POST /signup, or GET /path 404)"
                    )
                if not say:
                    raise SystemExit(
                        f"line {i}: @demo you goes right under the paragraph that tells the"
                        " viewer what to do"
                    )
                turn = say.pop()  # the paragraph just above is the viewer's turn
                emit_say()
                emit(
                    {
                        "kind": "you",
                        **turn,
                        "device": words[0],
                        "display": " ".join(words[1:]),  # what ends it, carried by its cue
                        "until": {
                            "method": words[1],
                            "path": words[2],
                            "status": int(words[3]) if len(words) == 4 else None,
                        },
                        "do": [],
                        "point": [words[0]],
                    }
                )
                if code_cue:  # the route the request reaches, beside the app's machine
                    steps[-1]["code"] = dict(code_cue)
                    code_cue = None
            elif verb == "do":
                words = arg.split()
                if not steps or steps[-1]["kind"] != "you":
                    raise SystemExit(f"line {i}: @demo do goes under a @demo you cue")
                if len(words) < 2 or words[0] not in DEVICES or words[1] not in ACTIONS:
                    raise SystemExit(
                        f"line {i}: @demo do names a device and one of {', '.join(ACTIONS)}"
                    )
                device, action, rest = words[0], words[1], words[2:]
                if action in ("click", "tick") and rest:
                    rest = [" ".join(rest)]  # a selector may have spaces: #devices button
                if len(rest) != ACTIONS[action] or (action == "copy" and rest[1] not in DEVICES):
                    raise SystemExit(
                        f"line {i}: @demo do {action} takes"
                        + {
                            "click": " a selector",
                            "tick": " a selector",
                            "copy": " a selector, a device and a selector",
                            "reload": " nothing",
                        }[action]
                    )
                steps[-1]["do"].append({"device": device, "action": action, "args": rest})
            elif verb == "point":
                emit_say()  # the paragraph just above is the step that points
                if steps and steps[-1]["kind"] in ("concept", "say", "you"):
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
            lang = info[0] if info else ""
            if lang == "python" and file_section is not None:
                file_section["text"] = "\n".join(body)
                file_section = None
            if not active:
                continue
            if commit_cue:
                at, names, rkey = commit_cue
                if lang != "json":
                    raise SystemExit(f"line {at}: @demo commit needs a ```json record under it")
                try:
                    value = json.loads("\n".join(body))
                except ValueError as e:
                    raise SystemExit(f"line {at}: the record under @demo commit: {e}") from None
                records = value if isinstance(value, list) else [value]
                if not records or not all(isinstance(r, dict) and r.get("$type") for r in records):
                    raise SystemExit(f"line {at}: each record under @demo commit needs a $type")
                if isinstance(value, list) and rkey:
                    raise SystemExit(f"line {at}: a sequence of records cannot share one key")
                what = records[0]["$type"] + (f" x{len(records)}" if len(records) > 1 else "")
                emit_say()  # words between the cue and its record are said before it is signed
                step = emit(
                    {
                        "kind": "commit",
                        "machines": names,
                        "display": what + (f" for {', '.join(names)}" if names else ""),
                    }
                )
                if isinstance(value, list):
                    step["records"] = records  # a sequence: one at a time, each heard out
                else:
                    step["record"] = value
                if rkey:
                    step["rkey"] = rkey
                if code_cue:
                    step["code"] = dict(code_cue)  # shown beside the first member
                    code_cue = None
                if fails_cue:
                    for n in fails_cue[1] or range(1, len(names) + 1):
                        if not 1 <= n <= len(names):
                            raise SystemExit(
                                f"line {fails_cue[0]}: @demo fails {n}, but the commit names"
                                f" {len(names)} members"
                            )
                    step["silent"] = [
                        names[n - 1] for n in fails_cue[1] or range(1, len(names) + 1)
                    ]
                commit_cue = fails_cue = None
                last_block = []  # a listing under it would be nobody's output
                recent_image = None
                replace_next_bash = False
                continue
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
                    recent_image = None  # a command between them: not the concept's picture
                    for s in block:
                        if code_cue and s["kind"] in ("run", "restart"):
                            s["code"] = dict(code_cue)  # the cue above said where this reaches
                        emit(s)
                    last_block = [s for s in block if s["kind"] == "run"]
                if fails_cue:
                    at, numbers = fails_cue
                    runs = [s for s in block if s["kind"] == "run"]
                    for n in numbers or range(1, len(runs) + 1):
                        if not 1 <= n <= len(runs):
                            raise SystemExit(
                                f"line {at}: @demo fails {n}, but the block has {len(runs)} commands"
                            )
                        runs[n - 1]["fails"] = True  # everything else is meant to exit 0
                code_cue = fails_cue = None
            elif lang in ("text", "json") and last_block and steps and steps[-1] is last_block[-1]:
                # what the guide says should come back, and from which commands: the
                # block's own, or the one a machine left running
                sources = list(last_block)  # the steps themselves: chapters renumber them later
                if prints_cue:
                    at, who = prints_cue
                    running = None  # what `who` has running now: a stop or a reboot ends it
                    for s in steps:
                        if s.get("machine") != who:
                            continue
                        if s["kind"] == "run" and s.get("bg"):
                            running = s
                        elif s["kind"] in ("stop", "restart"):
                            running = None
                    if running is None:
                        raise SystemExit(
                            f"line {at}: @demo prints {who}, and {who} has nothing running"
                        )
                    sources = [running]
                last_block[-1].setdefault("listings", []).append(
                    {"text": "\n".join(body), "from": sources}
                )
                prints_cue = None
            replace_next_bash = False
            continue

        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            if active and (m := CONCEPT.match(as_lines([q for q in quote if q]))):
                emit_say()
                concept: dict = {
                    "kind": "concept",
                    "style": STYLES[m.group(1)],
                    "title": m.group(2).rstrip("."),
                    "body": m.group(3),
                    "point": [],
                }
                if recent_image and recent_image[1] <= 1:  # the caption's picture, just above
                    concept["sketch"] = recent_image[0]
                recent_image = None
                emit(concept)
                # a concept that lists words defines them for the whole show: the page
                # marks each where a caption uses it and shows its line on hover
                for line in concept["body"].split("\n"):
                    if term := TERM.match(line):
                        glossary.append({"word": term.group(1), "text": term.group(2)})
            replace_next_bash = False
            continue

        if m := IMAGE.match(stripped):
            alt, path = m.group(1), m.group(2)
            images[path] = {"alt": alt}
            recent_image = [path, 0]
            if active:  # the picture goes with the words just above it
                if say:
                    say[-1]["sketch"] = path
                elif steps and steps[-1]["kind"] in ("say", "you"):
                    steps[-1]["sketch"] = path
                elif steps and steps[-1]["kind"] == "concept":
                    steps[-1]["sketch"] = path
                    recent_image = None  # a concept's own picture is nobody else's
            i += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            i += 1
            file_section = None
            recent_image = None
            if m := FILE_HEADING.match(stripped):
                file_section = files[m.group(1)] = {"gloss": "", "text": ""}
            if active:
                emit_say()  # what the section said last is still a caption
                emit({"kind": "heading", "level": level, "text": stripped.lstrip("#").strip()})
            replace_next_bash = False
            continue

        if stripped and not stripped.startswith("|"):
            para = []
            while (
                i < len(lines)
                and lines[i].strip()
                and not re.match(
                    r"^(#|>|```|\||!\[)", lines[i].strip()
                )  # a picture is its own line
                and not FULL_COMMENT.match(lines[i].strip())
            ):
                para.append(lines[i].strip())
                i += 1
            if not para:  # a bare comment that is no directive: step over it
                i += 1
                continue
            # typing prose out, spacing tidied: what is left is the caption
            text, in_terminal = without_terminal(as_lines(para), in_terminal)
            if file_section is not None and not file_section["gloss"] and not file_section["text"]:
                file_section["gloss"] = text  # what the file is for, in a line
            if active and text and not skip_next_para:
                say.append({"text": text})
                if recent_image:
                    recent_image[1] += 1
            skip_next_para = False
            replace_next_bash = False
            continue
        i += 1

    if commit_cue:
        raise SystemExit(f"line {commit_cue[0]}: @demo commit needs a ```json record under it")
    if app is not None and app["machine"] not in machines:
        raise SystemExit(
            f"line {app['line']}: @demo app is served by {app['machine']}, not in the cast"
        )
    if strangers := sorted(set(peers) - set(machines)):
        raise SystemExit(
            f"line {peer_lines[strangers[0]]}: @demo peer gives a program to {strangers[0]},"
            " who is not in the cast"
        )
    menu = chapters(steps)  # puts the steps in playing order and numbers them
    for step in steps:
        for listing in step.get("listings", []):  # now that every step has its number
            listing["from"] = [source["id"] for source in listing["from"]]
    return {
        "machines": machines,
        "peer": peer,
        "peers": {name: peers.get(name, peer) for name in machines},  # what each machine runs
        "app": None if app is None else {"machine": app["machine"], "path": app["path"]},
        "chapters": menu,
        "files": files,
        "images": images,
        "glossary": glossary,
        "steps": steps,
    }


def chapters(steps: list[dict]) -> list[dict]:
    """Put the steps in chapter order, in place, and say where each chapter starts.

    A guide without chapter cues is one unnamed chapter and plays as written.
    With them, every range belongs to a chapter, and the show plays the
    chapters by number: the code's chapter can come last on the screen while
    the page keeps it up front for a reader."""
    loose: list[dict] = []
    groups: dict[int, list[dict]] = {}  # a number cued again continues that chapter
    current: list[dict] | None = None
    for step in steps:
        if step["kind"] == "chapter":
            if step["number"] in groups:
                current = groups[step["number"]]
            else:
                current = groups[step["number"]] = [step]
        elif current is None:
            loose.append(step)
        else:
            current.append(step)
    if not groups:
        return []
    if loose:
        raise SystemExit("steps before the first chapter cue: every range needs a chapter")
    ordered = [groups[n] for n in sorted(groups)]
    steps[:] = [s for g in ordered for s in g]
    for i, step in enumerate(steps):
        step["id"] = i
    return [
        {
            "number": g[0]["number"],
            "title": g[0]["title"],
            "blurb": g[0].get("blurb", ""),
            "start": g[0]["id"],
            "concepts": [s["title"] for s in g if s["kind"] == "concept"],
            "presses": sum(1 for s in g if s["kind"] in VISIBLE),
            "commands": sum(1 for s in g if s["kind"] == "run"),
            "turns": sum(1 for s in g if s["kind"] == "you"),  # what the viewer does in the app
        }
        for g in ordered
    ]


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        manifest = parse(f.read())
    manifest["source"] = sys.argv[1]
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=1)
