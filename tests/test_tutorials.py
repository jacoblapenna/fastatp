"""The tutorials are the repo's: their guides quote the lab's files, and the
conductor plays the guides as scores. Both must hold without Docker."""

import importlib.util
import sys
from itertools import pairwise
from pathlib import Path

import pytest

TUTORIALS = Path(__file__).resolve().parent.parent / "tutorials"
TOOLS = TUTORIALS / "tools"

# The source archive leaves tutorials/ out, so from an sdist there is nothing to check.
pytestmark = pytest.mark.skipif(
    not TUTORIALS.exists(), reason="tutorials/ is the repo's, not the sdist's"
)


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("guide", ["GETTING_STARTED.md"])
def test_a_guide_quotes_the_lab_files_exactly(guide):
    """`### \\`name.py\\`` sections show lab/name.py in full. When the lab
    changes, `python tutorials/tools/md_embed.py --write <guide>` updates the
    page; this is what makes the prose and the code one thing."""
    md_embed = _tool("md_embed")
    drifted = md_embed.sync(TUTORIALS / guide, TUTORIALS / "lab", write=False)
    assert drifted == [], f"{guide} differs from lab/ in: {drifted} (run md_embed.py --write)"
    assert len(md_embed.embedded_blocks((TUTORIALS / guide).read_text())) >= 8


def test_getting_started_is_a_playable_score():
    """The conductor's score is the markdown itself: every ./lab.sh line is a
    step for one of the cast, and the directives parse."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    assert score["machines"] == ["alice", "bob", "eve"]
    kinds = [s["kind"] for s in score["steps"]]
    assert kinds.count("run") > 30 and "restart" in kinds and "concept" in kinds
    machines = {s["machine"] for s in score["steps"] if s["kind"] == "run"}
    assert machines == {"alice", "bob", "eve"}
    captures = [s["capture"] for s in score["steps"] if "capture" in s]
    assert "CH" in captures and "AT" in captures  # the channel DID and alice's tunnel address
    # prose for the person at a terminal is not a caption on the screen
    captions = " ".join(s["text"] for s in score["steps"] if s["kind"] == "say")
    assert "Ctrl-C" not in captions and "second terminal" not in captions
    assert "<!--" not in captions and "Your rkey will differ" not in captions
    assert not any("\n\n" in s["text"] for s in score["steps"] if s["kind"] == "say")
    # the words come first: a viewer who only pressed Start meets the handful
    # the first chapter needs before any command uses them, and lists keep
    # their lines; every other word is in the glossary, a hover away
    concepts = [s for s in score["steps"] if s["kind"] == "concept"]
    assert concepts[0]["title"].startswith("the words in this guide")
    assert 4 <= concepts[0]["body"].count("\n- **") + 1 <= 8
    assert kinds.index("concept") < kinds.index("run")
    # four chapters, played by number, each with commands in it
    assert [c["number"] for c in score["chapters"]] == [1, 2, 3, 4]
    assert [c["title"] for c in score["chapters"]][2:] == [
        "The private side",
        "Admission and eviction",
    ]
    assert all(c["blurb"] and c["concepts"] and c["commands"] for c in score["chapters"])
    # the pictures under the key concepts are the guide's own images, and exist
    named = [s["sketch"] for s in score["steps"] if s["kind"] == "concept" and "sketch" in s]
    assert len(named) >= 9 and len(set(named)) == len(named)
    for path in named:
        assert path in score["images"] and score["images"][path]["alt"]
        assert (TUTORIALS / path).is_file()
    # the opening caption and the two drawings in the prose are pictures too
    captioned = [s["sketch"] for s in score["steps"] if s["kind"] == "say" and "sketch" in s]
    assert "sketches/lan.svg" in captioned and "sketches/block-store.svg" in captioned
    # a question every handful of presses, answered by what is shown next: a
    # command or a key concept, never told by a caption
    asks = [i for i, s in enumerate(score["steps"]) if s["kind"] == "ask"]
    assert len(asks) >= 20
    presses = [i for i, s in enumerate(score["steps"]) if s["kind"] in md_manifest.VISIBLE]
    gaps = [presses.index(b) - presses.index(a) for a, b in pairwise(asks)]
    assert max(gaps) <= 14, gaps
    for i in asks:
        assert "?" in score["steps"][i]["text"]
        following = next(
            s
            for s in score["steps"][i + 1 :]
            if s["kind"] in ("run", "restart", "say", "concept", "ask")
        )
        assert following["kind"] in ("run", "restart", "concept"), score["steps"][i]["text"]
    # no picture has text running past its edge
    sketch_check = _tool("sketch_check")
    for path in score["images"]:
        assert sketch_check.overruns(str(TUTORIALS / path)) == []
    starts = [c["start"] for c in score["chapters"]] + [len(score["steps"])]
    for a, b in pairwise(starts):
        assert any(s["kind"] == "run" for s in score["steps"][a:b])
    assert score["steps"][0]["kind"] == "chapter" and score["steps"][0]["number"] == 1
    # the Setup key concepts play where the thing they explain happens
    titles = [s["title"] for s in score["steps"] if s["kind"] == "concept"]
    first_commit = next(
        i for i, s in enumerate(score["steps"]) if s["kind"] == "run" and "/commit" in s["cmd"]
    )
    lexicon = next(
        s["id"]
        for s in score["steps"]
        if s["kind"] == "concept" and s["title"].startswith("a lexicon")
    )
    assert first_commit < lexicon < starts[1]
    assert any(t.startswith("authorization in FastATP is declarative") for t in titles)
    # every lab file is known, with the guide's line on what it is for, and a
    # command shows the file it runs beside its machine
    assert set(score["files"]) >= {
        "lexicons.py",
        "dids.py",
        "peer.py",
        "pair.py",
        "follow.py",
        "tail.py",
        "verify.py",
        "blocks.py",
    }
    assert all(f["gloss"] and f["text"] for f in score["files"].values())
    shown = {s["code"]["file"] for s in score["steps"] if s["kind"] == "run" and "code" in s}
    assert {"peer.py", "follow.py", "tail.py", "verify.py", "blocks.py", "pair.py"} <= shown
    assert all(s["code"]["file"] in score["files"] for s in score["steps"] if s.get("code"))
    write = next(s for s in score["steps"] if s["kind"] == "run" and "/commit" in s["cmd"])
    assert write["code"] == {"file": "peer.py", "focus": "async def commit("}  # the cue above it
    for s in score["steps"]:
        if s.get("code", {}).get("focus"):
            assert s["code"]["focus"] in score["files"][s["code"]["file"]]["text"]
    # nothing a section says last is lost: its closing paragraph is a caption
    captions_list = [s["text"] for s in score["steps"] if s["kind"] == "say"]
    assert any(c.startswith("That is a conversation") for c in captions_list)
    # a caption can point at what it talks about, and a cue can keep a fold open
    assert any(s.get("point") for s in score["steps"] if s["kind"] == "say")
    assert any(s["kind"] == "layout" and s["action"] == "open" for s in score["steps"])


def test_a_cue_carries_its_whole_step():
    """The conductor's repo alone is the show: every kind of step, signed as
    a cue, carries what the screen needs (the words, the targets, the code),
    not a step number into a guide the reader may not have."""
    sys.path.insert(0, str(TUTORIALS / "lab"))
    try:
        backstage = __import__("backstage")
    finally:
        sys.path.pop(0)
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    seen = set()
    for step in score["steps"]:
        cue = backstage.Cue.for_step(
            step, show="show", to="did:key:z", command=step.get("cmd", ""), env={"CH": "x"}
        )
        kind = step["kind"]
        seen.add(cue.action)
        if kind in ("say", "ask"):
            assert cue.text == step["text"]
        elif kind == "concept":
            assert cue.title == step["title"] and cue.text == step["body"]
            assert cue.style in ("key", "important", "tip")
            assert cue.sketch == step.get("sketch", "")
        elif kind == "heading":
            assert cue.text == step["text"] and cue.level == step["level"]
        elif kind == "layout":
            assert cue.action == step["action"] and cue.targets
        elif kind == "wait":
            assert cue.wait_ms == int(step["seconds"] * 1000)
        elif kind == "chapter":
            assert cue.chapter == step["number"] and cue.title == step["title"]
        elif kind == "run":
            assert cue.env == {"CH": "x"} and cue.display == step["display"]
            if "code" in step:
                assert cue.file == step["code"]["file"] and cue.focus == step["code"]["focus"]
        if step.get("point"):
            assert cue.targets == step["point"]
    assert {
        "say",
        "concept",
        "heading",
        "run",
        "stop",
        "restart",
        "wait",
        "collapse",
        "open",
        "chapter",
        "ask",
    } <= seen


def test_the_words_a_concept_defines_are_the_shows_glossary():
    """A key concept that lists words, one `- **Word**: line` each, defines them for the
    whole show, and so does a glossary region the show never says: the page marks each
    word where a caption uses it and shows its line on hover."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    words = {g["word"]: g["text"] for g in score["glossary"]}
    assert {"Peer", "DID document", "Commit", "Channel", "Doorman"} <= set(words)  # concepts
    assert {"Pen", "Tunnel", "Kernel", "Loopback"} <= set(words)  # the glossary region
    assert not {"Drop", "Hub"} & set(words)  # a drop is a commit; the host of a channel is a peer
    assert len(words) == len(score["glossary"])  # no word defined twice
    captions = " ".join(s["text"] for s in score["steps"] if s["kind"] == "say")
    assert "dotted word" not in captions  # the glossary is never said
    assert all(text and "\n" not in text for text in words.values())


def test_the_score_says_how_each_command_is_meant_to_end():
    """A `fails` cue marks the commands of the next block that are meant to be
    refused; every other command is meant to exit 0. The headless play holds
    each command to it, so a refusal that stops refusing fails the run."""
    md_manifest = _tool("md_manifest")
    page = """<!-- @demo machines alice bob -->
<!-- @demo start -->
<!-- @demo fails 2 -->
```bash
./lab.sh alice true
./lab.sh bob false
```

```bash
./lab.sh bob true
```
<!-- @demo end -->
"""
    runs = [s for s in md_manifest.parse(page)["steps"] if s["kind"] == "run"]
    assert [bool(s.get("fails")) for s in runs] == [False, True, False]
    with pytest.raises(SystemExit, match="fails 3"):
        md_manifest.parse(page.replace("fails 2", "fails 3"))

    play = _tool("play")
    works, refused, left_running = {}, {"fails": True}, {"bg": True}
    assert play.verdict(works, 0) is None
    assert play.verdict(refused, 1) is None
    assert play.verdict(left_running, -15) is None  # the guide's own Ctrl-C
    assert "says this works" in play.verdict(works, 1)
    assert "says this fails" in play.verdict(refused, 0)
    assert "says this fails" in play.verdict(refused, -15)
    assert "took too long" in play.verdict(works, -15)
    # a refusal counts only if the command ran: a typo or a player that tripped is not one
    assert "never ran" in play.verdict(refused, 127)
    assert "could not run" in play.verdict(refused, 1, ["[player] could not run this: OSError()"])
    assert "could not run" in play.verdict(works, 1, ["[player] restarted while this ran"])

    guide = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    meant = [s["cmd"].split()[0] for s in guide["steps"] if s.get("fails")]
    assert meant and set(meant) <= {"python", "ping"}  # followers refused, pings unanswered


def test_the_favicon_is_the_brand_goose():
    """The tab icon is a static file, so it carries its own copy of the goose: the same path."""
    brand = (TUTORIALS / "conductor" / "src" / "brand" / "brand.js").read_text()
    goose = brand.split("export const goose =")[1].split("'")[1]
    assert f'd="{goose}"' in (TUTORIALS / "conductor" / "public" / "favicon.svg").read_text()
