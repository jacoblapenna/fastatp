"""The tutorials are the repo's: their guides quote the lab's files, and the
conductor plays the guides as scores. Both must hold without Docker."""

import importlib.util
import sys
from itertools import pairwise
from pathlib import Path

import pytest

TUTORIALS = Path(__file__).resolve().parent.parent / "tutorials"
TOOLS = TUTORIALS / "tools"


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
    # the words come first: a viewer who only pressed Start meets the
    # vocabulary before any command uses it, and lists keep their lines
    concepts = [s for s in score["steps"] if s["kind"] == "concept"]
    assert concepts[0]["title"].startswith("the words in this guide")
    assert concepts[0]["body"].count("\n- **") >= 12
    assert kinds.index("concept") < kinds.index("run")
    # four chapters, played by number, each with commands in it
    assert [c["number"] for c in score["chapters"]] == [1, 2, 3, 4]
    assert [c["title"] for c in score["chapters"]][2:] == [
        "The private side",
        "Admission and eviction",
    ]
    assert all(c["blurb"] and c["concepts"] and c["commands"] for c in score["chapters"])
    starts = [c["start"] for c in score["chapters"]] + [len(score["steps"])]
    for a, b in pairwise(starts):
        assert any(s["kind"] == "run" for s in score["steps"][a:b])
    assert score["steps"][0]["kind"] == "chapter" and score["steps"][0]["number"] == 1
    # the Setup key concepts play where the thing they explain happens
    titles = [s["title"] for s in score["steps"] if s["kind"] == "concept"]
    first_drop = next(
        i for i, s in enumerate(score["steps"]) if s["kind"] == "run" and "/drop" in s["cmd"]
    )
    lexicon = next(
        s["id"]
        for s in score["steps"]
        if s["kind"] == "concept" and s["title"].startswith("a lexicon")
    )
    assert first_drop < lexicon < starts[1]
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
    drop = next(s for s in score["steps"] if s["kind"] == "run" and "/drop" in s["cmd"])
    assert drop["code"] == {"file": "peer.py", "focus": "async def drop("}
    assert drop["code"]["focus"] in score["files"]["peer.py"]["text"]
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
        if kind == "say":
            assert cue.text == step["text"]
        elif kind == "concept":
            assert cue.title == step["title"] and cue.text == step["body"]
            assert cue.style in ("key", "important", "tip")
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
    } <= seen
