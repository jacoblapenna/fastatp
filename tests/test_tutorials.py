"""The tutorials are the repo's: their guides quote the lab's files, and the
conductor plays the guides as scores. Both must hold without Docker."""

import importlib.util
import re
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


# Every guide, and how many lab files it quotes in full. CI plays each one
# (the tutorial job in .github/workflows/test.yml has the same list).
GUIDES = {"GETTING_STARTED.md": 8, "CONDUCTOR_AND_ORCHESTRA.md": 4, "HOOT.md": 8}

# The steps the headless play holds to an outcome: a command that must end as
# the guide says, a reboot that must come back, a committed record whose
# members must answer (or stay silent). A chapter is one of these or more.
OUTCOMES = ("run", "restart", "commit")


@pytest.mark.parametrize("guide", GUIDES)
def test_a_guide_quotes_the_lab_files_exactly(guide):
    """`### \\`name.py\\`` sections show lab/name.py in full. When the lab
    changes, `python tutorials/tools/md_embed.py --write <guide>` updates the
    page; this is what makes the prose and the code one thing."""
    md_embed = _tool("md_embed")
    drifted = md_embed.sync(TUTORIALS / guide, TUTORIALS / "lab", write=False)
    assert drifted == [], f"{guide} differs from lab/ in: {drifted} (run md_embed.py --write)"
    assert len(md_embed.embedded_blocks((TUTORIALS / guide).read_text())) >= GUIDES[guide]


@pytest.mark.parametrize("guide", GUIDES)
def test_a_guide_is_a_playable_score(guide):
    """What every guide's score keeps, whatever it teaches: the words first, a
    question every handful of presses answered by the next step, pictures
    that exist and fit, code cues that point at real lines, and chapters that
    each do something on the machines the headless play holds to an outcome."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / guide).read_text())
    steps = score["steps"]
    kinds = [s["kind"] for s in steps]
    # a cast the lab has, each machine running a program the lab has
    lab = TUTORIALS / "lab"
    known = re.search(r'^MACHINES="([^"]+)"', (lab / "lab.sh").read_text(), re.M).group(1).split()
    assert score["machines"] and set(score["machines"]) <= set(known)
    assert all((lab / program).is_file() for program in score["peers"].values())
    assert steps[0]["kind"] == "chapter" and steps[0]["number"] == 1
    # prose for the person at a terminal is not a caption on the screen
    captions = [s["text"] for s in steps if s["kind"] == "say"]
    assert not any("Ctrl-C" in c or "second terminal" in c or "<!--" in c for c in captions)
    assert not any("\n\n" in c for c in captions)
    # the words come first: a viewer who only pressed Start meets the handful
    # the first chapter needs before anything acts on the machines; every other
    # word is in the glossary, a hover away
    concepts = [s for s in steps if s["kind"] == "concept"]
    assert concepts[0]["title"].startswith("the words in this guide")
    assert 4 <= concepts[0]["body"].count("\n- **") + 1 <= 8
    assert kinds.index("concept") < min(kinds.index(k) for k in OUTCOMES if k in kinds)
    # chapters played by number, each with its card's words, a key concept,
    # and something the play checks
    assert [c["number"] for c in score["chapters"]] == list(range(1, len(score["chapters"]) + 1))
    assert all(c["blurb"] and c["concepts"] for c in score["chapters"])
    starts = [c["start"] for c in score["chapters"]] + [len(steps)]
    for a, b in pairwise(starts):
        assert any(s["kind"] in OUTCOMES for s in steps[a:b]), steps[a]["title"]
    # the pictures under the key concepts are the guide's own images, exist, and fit
    named = [s["sketch"] for s in concepts if "sketch" in s]
    assert named and len(set(named)) == len(named)
    sketch_check = _tool("sketch_check")
    for path in score["images"]:
        assert score["images"][path]["alt"] and (TUTORIALS / path).is_file()
        assert sketch_check.overruns(str(TUTORIALS / path)) == []
    # a question every handful of presses, answered by what is shown next:
    # something on the machines or a key concept, never told by a caption
    asks = [i for i, s in enumerate(steps) if s["kind"] == "ask"]
    presses = [i for i, s in enumerate(steps) if s["kind"] in md_manifest.VISIBLE]
    gaps = [presses.index(b) - presses.index(a) for a, b in pairwise(asks)]
    assert asks and max(gaps, default=0) <= 14, gaps
    for i in asks:
        assert "?" in steps[i]["text"]
        following = next(
            s for s in steps[i + 1 :] if s["kind"] in (*OUTCOMES, "say", "concept", "ask")
        )
        assert following["kind"] in (*OUTCOMES, "concept"), steps[i]["text"]
    # every lab file is known, with the guide's line on what it is for, and a
    # code cue names a file the guide shows, at a line that is in it
    assert score["files"] and all(f["gloss"] and f["text"] for f in score["files"].values())
    for s in steps:
        if code := s.get("code"):
            assert code["file"] in score["files"]
            assert code["focus"] in score["files"][code["file"]]["text"]


def test_getting_started_is_a_playable_score():
    """The conductor's score is the markdown itself: every ./lab.sh line is a
    step for one of the cast, and the directives parse."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    kinds = [s["kind"] for s in score["steps"]]
    assert kinds.count("run") > 30 and "restart" in kinds and "concept" in kinds
    machines = {s["machine"] for s in score["steps"] if s["kind"] == "run"}
    assert machines == {"alice", "bob", "eve"}
    captures = [s["capture"] for s in score["steps"] if "capture" in s]
    assert "CH" in captures and "AT" in captures  # the channel DID and alice's tunnel address
    captions = " ".join(s["text"] for s in score["steps"] if s["kind"] == "say")
    assert "Your rkey will differ" not in captions
    assert score["machines"] == ["alice", "bob", "eve"]
    assert score["peer"] == "peer.py"  # guide one's machines run the plain peer
    assert score["peers"] == {"alice": "peer.py", "bob": "peer.py", "eve": "peer.py"}
    # four chapters
    assert [c["title"] for c in score["chapters"]][2:] == [
        "The private side",
        "Admission and eviction",
    ]
    assert all(c["commands"] for c in score["chapters"])
    named = [s["sketch"] for s in score["steps"] if s["kind"] == "concept" and "sketch" in s]
    assert len(named) >= 9
    # the opening caption and the two drawings in the prose are pictures too
    captioned = [s["sketch"] for s in score["steps"] if s["kind"] == "say" and "sketch" in s]
    assert "sketches/lan.svg" in captioned and "sketches/block-store.svg" in captioned
    asks = [s for s in score["steps"] if s["kind"] == "ask"]
    assert len(asks) >= 20
    starts = [c["start"] for c in score["chapters"]]
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
    # every lab file is known, and a command shows the file it runs beside its machine
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
    shown = {s["code"]["file"] for s in score["steps"] if s["kind"] == "run" and "code" in s}
    assert {"peer.py", "follow.py", "tail.py", "verify.py", "blocks.py", "pair.py"} <= shown
    write = next(s for s in score["steps"] if s["kind"] == "run" and "/commit" in s["cmd"])
    assert write["code"] == {"file": "peer.py", "focus": "async def commit("}  # the cue above it
    # nothing a section says last is lost: its closing paragraph is a caption
    captions_list = [s["text"] for s in score["steps"] if s["kind"] == "say"]
    assert any(c.startswith("That is a conversation") for c in captions_list)
    # a caption can point at what it talks about, and a cue can keep a fold open
    assert any(s.get("point") for s in score["steps"] if s["kind"] == "say")
    assert any(s["kind"] == "layout" and s["action"] == "open" for s in score["steps"])


@pytest.mark.parametrize("guide", GUIDES)
def test_a_cue_carries_its_whole_step(guide):
    """The conductor's repo alone is the show: every kind of step, signed as
    a cue, carries what the screen needs (the words, the targets, the code),
    not a step number into a guide the reader may not have."""
    sys.path.insert(0, str(TUTORIALS / "lab"))
    try:
        backstage = __import__("backstage")
    finally:
        sys.path.pop(0)
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / guide).read_text())
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
        elif kind == "commit":
            assert cue.targets == step["machines"] and cue.display == step["display"]
        if "code" in step:
            assert cue.file == step["code"]["file"] and cue.focus == step["code"]["focus"]
        if step.get("point"):
            assert cue.targets == step["point"]
    if guide == "CONDUCTOR_AND_ORCHESTRA.md":
        assert {"say", "concept", "heading", "run", "restart", "commit", "chapter", "ask"} <= seen
        return
    if guide == "HOOT.md":
        assert {"say", "concept", "heading", "run", "wait", "chapter", "ask"} <= seen
        return
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


def test_a_listing_is_held_to_what_the_commands_print():
    """The listing under a command block is an excerpt of what its commands
    print. The headless play compares the two: every line the guide shows
    must have been printed, in order; values that are new on every run are
    compared by shape; and nothing else is forgiven."""
    md_manifest = _tool("md_manifest")
    play = _tool("play")
    page = """<!-- @demo machines alice bob -->
<!-- @demo start -->
```bash
./lab.sh bob python follow.py did:web:alice.lan alice.lan
```

```bash
./lab.sh alice true
./lab.sh alice false
```

```text
the block's own
```

<!-- @demo prints bob -->
```text
what bob's follower went on to print
```
<!-- @demo end -->
"""
    runs = [s for s in md_manifest.parse(page)["steps"] if s["kind"] == "run"]
    follower, first, second = (s["id"] for s in runs)
    assert runs[2]["listings"] == [
        {"text": "the block's own", "from": [first, second]},
        {"text": "what bob's follower went on to print", "from": [follower]},
    ]
    with pytest.raises(SystemExit, match="has nothing running"):
        md_manifest.parse(page.replace("prints bob", "prints alice"))
    # a command that was stopped is not on anyone's screen any more
    stopped = page.replace(
        "```bash\n./lab.sh alice true", "<!-- @demo stop bob -->\n```bash\n./lab.sh alice true"
    )
    assert stopped != page
    with pytest.raises(SystemExit, match="has nothing running"):
        md_manifest.parse(stopped)

    printed = [
        "checkpoint: rev 3n2r4ij7vh5mg, 1 records, verified; stream at seq 3",
        "   com.example.tutorial.note/3n2r4ij7o44fx: Note(text='hello, LAN',"
        " created_at='2026-11-20T17:31:23.671643Z')",
        '{"cid":',
        '    "bafyreieoherpgtc7hnql6zstcfnrhznqccf45zk53aylattr3dvzph7fzq"}',
        "  latest handshake: 6 seconds ago",
        "  transfer: 964 B received, 316 B sent",
    ]
    agrees = """checkpoint: rev 3mz23h6bwy242, 1 records, verified; stream at seq 3
   com.example.tutorial.note/3mz23h6bv4dmd: Note(text='hello, LAN', ...)
{"cid": "bafyreifoptz..."}
  latest handshake: 7 seconds ago
  transfer: 212 B received, 92 B sent"""
    assert play.listing_problems(agrees, printed) == []
    # a count is not a value that changes from run to run: it is compared exactly
    assert play.listing_problems("checkpoint: rev 3mz23h6bwy242, 2 records", printed) == [
        "checkpoint: rev 3mz23h6bwy242, 2 records"
    ]
    # the guide's order is part of what it claims
    assert play.listing_problems("latest handshake: 7 seconds ago\ncheckpoint: rev", printed)
    # what a listing leaves out of a line, it marks with ...
    assert play.listing_problems("checkpoint: rev 3mz23h6bwy242, verified", printed)
    assert play.listing_problems("checkpoint: rev 3mz23h6bwy242, ..., verified", printed) == []

    guide = md_manifest.parse((TUTORIALS / "GETTING_STARTED.md").read_text())
    listings = [x for s in guide["steps"] for x in s.get("listings", [])]
    # the count is the guide's own: a listing added or removed changes it on purpose,
    # and one the parser stops seeing fails here and not silently in the play
    assert len(listings) == 24 and all(x["from"] for x in listings)


def test_the_favicon_is_the_brand_goose():
    """The tab icon is a static file, so it carries its own copy of the goose: the same path."""
    brand = (TUTORIALS / "conductor" / "src" / "brand" / "brand.js").read_text()
    goose = brand.split("export const goose =")[1].split("'")[1]
    assert f'd="{goose}"' in (TUTORIALS / "conductor" / "public" / "favicon.svg").read_text()


def test_an_orchestra_member_plays_only_its_own_parts_and_only_what_it_can_hear(monkeypatch):
    """orchestra.py reads a Play with its own model, table and rules: it
    writes a Tone for each part naming it, with the pitch its own table gives
    and the length the Tempo gives, at a path fixed by the Play and the part
    (so playing is idempotent), and skips what it refuses instead of obeying."""
    from fractions import Fraction
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(TUTORIALS / "lab"))
    # orchestra.py imports this machine's peer.py, which needs WireGuard; stand in for it
    monkeypatch.setitem(sys.modules, "peer", SimpleNamespace())
    monkeypatch.delitem(sys.modules, "orchestra", raising=False)
    music = __import__("music")
    orchestra = __import__("orchestra")

    alice, bob = "did:web:alice.lan", "did:web:bob.lan"

    def part(member, note, octave=4, ticks=4):
        return {"member": member, "note": note, "octave": octave, "ticks": ticks}

    play = {
        "$type": music.Play.nsid,
        "parts": [
            part(alice, "do"),
            part(bob, "mi"),
            part(alice, "do", octave=12),  # about 67 kHz: alice's rule says no
            part(alice, "sol", octave=3, ticks=2),
            part(alice, "zap"),  # not in alice's table
        ],
    }
    path = f"{music.Play.nsid}/3abc"
    # an octave past hearing is turned down before any pitch is worked out,
    # however many digits it has
    huge = {"$type": music.Play.nsid, "parts": [part(alice, "do", octave=10**9)]}
    assert orchestra.my_parts(path, huge, alice, tick_ms=250) == []
    tones = orchestra.my_parts(path, play, alice, tick_ms=250)
    assert [rkey for rkey, _ in tones] == ["3abc-0", "3abc-3"]
    do, low_sol = tones[0][1], tones[1][1]
    assert (do.text, do.pitch, do.pitch_exponent, do.duration_ms) == ("do", 261626, -3, 1000)
    assert do.play == f"at://{orchestra.CONDUCTOR}/{path}"
    assert music.hertz(do.pitch, do.pitch_exponent) == Fraction(261626, 1000)
    # an octave down halves 391995 mHz; alice rounds the half, and alice's Tone says what was played
    assert (low_sol.pitch, low_sol.duration_ms) == (195998, 500)
    # a faster tempo: the same Play, shorter notes
    assert [t.duration_ms for _, t in orchestra.my_parts(path, play, alice, tick_ms=120)] == [
        480,
        240,
    ]
    # a Play this model refuses: nothing to do
    broken = {**play, "parts": [part(alice, "do", ticks=0)]}  # ticks must be at least 1
    assert orchestra.my_parts(f"{music.Play.nsid}/3abd", broken, alice, tick_ms=250) == []

    # Tone is Note with more: the exported lexicon keeps Note's fields and adds the rest
    tone = music.Tone.to_lexicon_json()["defs"]["main"]["record"]
    assert {"text", "created_at", "play", "pitch", "pitch_exponent", "duration_ms"} <= set(
        tone["properties"]
    )
    assert tone["properties"]["pitch"]["minimum"] == 1
    tempo = music.Tempo.to_lexicon_json()["defs"]["main"]["record"]
    assert tempo["properties"]["tick_ms"]["minimum"] == 1


def test_a_commit_cue_signs_a_record_for_its_members_and_waits_for_their_answers():
    """`@demo commit alice bob` above a ```json record is one step: the
    conductor signs that record into its own repo for those members, and
    each answers in its own repo. A fails cue names who is meant to stay
    silent. Getting it wrong stops the parse, not the show."""
    md_manifest = _tool("md_manifest")
    play = _tool("play")

    def score(
        cue: str, block: str = '```json\n{"$type": "com.example.tutorial.play"}\n```'
    ) -> dict:
        return md_manifest.parse(
            f"<!-- @demo machines alice bob eve -->\n<!-- @demo start -->\n\n{cue}\n{block}\n"
            "\n<!-- @demo end -->\n"
        )

    steps = score("<!-- @demo commit alice bob -->")["steps"]
    commit = next(s for s in steps if s["kind"] == "commit")
    assert commit["machines"] == ["alice", "bob"]
    assert commit["record"] == {"$type": "com.example.tutorial.play"}
    assert "silent" not in commit
    assert "commit" in md_manifest.VISIBLE  # one press of Next

    # for nobody in particular, at a fixed key: a setting every member reads
    setting = next(
        s for s in score("<!-- @demo commit at self -->")["steps"] if s["kind"] == "commit"
    )
    assert (setting["machines"], setting["rkey"]) == ([], "self")

    # words between the cue and its record are a caption before the commit, not after
    told = score("<!-- @demo commit alice -->\n\nAlice, play.\n")["steps"]
    assert [s["kind"] for s in told if s["kind"] in ("say", "commit")] == ["say", "commit"]

    quiet = score("<!-- @demo fails 2 -->\n<!-- @demo commit alice bob -->")["steps"]
    assert next(s for s in quiet if s["kind"] == "commit")["silent"] == ["bob"]

    for cue, block in [
        ("<!-- @demo commit mallory -->", None),  # not in the cast
        ("<!-- @demo commit alice at -->", None),  # at, and no key
        ("<!-- @demo commit at self alice -->", None),  # members come before at
        ("<!-- @demo commit alice at self -->", None),  # a setting names no members
        ("<!-- @demo commit alice -->", "```bash\n./lab.sh alice ls\n```"),  # no record
        ("<!-- @demo commit alice -->", "```json\n{not json}\n```"),
        ("<!-- @demo commit alice -->", '```json\n{"text": "no type"}\n```'),
        ("<!-- @demo fails 3 -->\n<!-- @demo commit alice bob -->", None),
    ]:
        with pytest.raises(SystemExit):
            score(cue, block) if block else score(cue)

    # the headless play holds the answers to the guide
    assert play.commit_verdict(commit, []) is None
    assert "no answer from bob" in play.commit_verdict(commit, ["bob"])
    quiet_step = {"silent": ["bob"]}
    assert play.commit_verdict(quiet_step, ["bob"]) is None
    assert "stays silent, and answered" in play.commit_verdict(quiet_step, [])
    # in a sequence, a record that does not name bob does not expect bob's silence
    assert play.commit_verdict(quiet_step, [], meant=[]) is None

    # an answer is any record that names the request's URI, wherever it keeps it
    sys.path.insert(0, str(TUTORIALS / "lab"))
    try:
        backstage = __import__("backstage")
    finally:
        sys.path.pop(0)
    uri = "at://did:web:conductor.stage/com.example.tutorial.play/3abc"
    assert backstage.mentions({"play": uri, "text": "do"}, uri)
    assert backstage.mentions({"parts": [{"source": {"uri": uri}}]}, uri)
    assert not backstage.mentions({"play": uri + "x", "text": uri[:-1]}, uri)


def test_the_conductor_and_the_orchestra_is_a_playable_score(monkeypatch):
    """Guide two's machines run orchestra.py, and what it asks of them is
    records, not commands: every Play it commits is one the members' own
    model accepts and names only the cast, the scale and the tune are
    sequences the conductor conducts one record at a time, and bob's part is
    the one meant to go unanswered while bob is offline."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "CONDUCTOR_AND_ORCHESTRA.md").read_text())
    steps = score["steps"]
    assert score["machines"] == ["alice", "bob", "eve"]
    assert score["peer"] == "orchestra.py"
    assert set(score["peers"].values()) == {"orchestra.py"}
    assert [c["title"] for c in score["chapters"]] == [
        "Backstage",
        "The orchestra",
        "Scales and chords",
        "The show is a repo",
    ]
    assert set(score["files"]) == {"trace.py", "music.py", "orchestra.py", "replay.py"}

    monkeypatch.syspath_prepend(str(TUTORIALS / "lab"))
    music = __import__("music")
    cast = {f"did:web:{m}.lan": m for m in score["machines"]}
    commits = [s for s in steps if s["kind"] == "commit"]
    for step in commits:
        records = step.get("records") or [step["record"]]
        for record in records:
            model = {music.Play.nsid: music.Play, music.Tempo.nsid: music.Tempo}[record["$type"]]
            model.model_validate(record)  # what a member's own model will make of it
            for part in record.get("parts", []):
                assert cast[part["member"]] in step["machines"]  # named, and cued for
    sequences = [s for s in commits if "records" in s]
    assert [len(s["records"]) for s in sequences] == [8, 30]  # the scale, then the tune
    settings = [s for s in commits if not s["machines"]]
    assert settings and all(s.get("rkey") == "self" for s in settings)  # tempo, in place
    assert [s["silent"] for s in commits if s.get("silent")] == [["bob"]]  # offline, on cue
    offline = next(i for i, s in enumerate(steps) if "pkill" in s.get("cmd", ""))
    back = next(i for i, s in enumerate(steps) if s["kind"] == "restart")
    silent = next(i for i, s in enumerate(steps) if s.get("silent"))
    assert offline < silent < back and steps[back]["machine"] == "bob"


def test_a_guide_names_its_cast_and_what_each_machine_runs():
    """`@demo machines` is the cast, the machines that boot and no others;
    `@demo peer` gives every machine a program, or one machine its own, and a
    machine's own wins. lab.sh boots only machines compose.yml defines."""
    md_manifest = _tool("md_manifest")

    def score(cues: str) -> dict:
        return md_manifest.parse(f"<!-- @demo machines alice bank cab-17 -->\n{cues}\n")

    assert score("")["peers"] == {"alice": "peer.py", "bank": "peer.py", "cab-17": "peer.py"}
    peers = score("<!-- @demo peer orchestra.py -->\n<!-- @demo peer bank bank.py -->")["peers"]
    assert peers == {"alice": "orchestra.py", "bank": "bank.py", "cab-17": "orchestra.py"}
    for bad in (
        "<!-- @demo peer eve eve.py -->",  # not in the cast
        "<!-- @demo peer bank -->",  # a machine, and no program
        "<!-- @demo peer bank bank.py extra -->",
        "<!-- @demo peer ../peer.py -->",  # a file name, not a path
    ):
        with pytest.raises(SystemExit):
            score(bad)

    # the machines lab.sh will boot are the ones compose.yml defines
    lab = TUTORIALS / "lab"
    listed = re.search(r'^MACHINES="([^"]+)"', (lab / "lab.sh").read_text(), re.M)
    # a machine is a service with a name on the LAN; the tap and the conductor are not
    compose = (lab / "compose.yml").read_text()
    services = re.findall(r"^    hostname: ([a-z0-9-]+)\.lan$", compose, re.M)
    assert listed and set(listed.group(1).split()) == set(services)


def test_hoot_is_a_playable_score():
    """Guide three's cast is its own: six machines, each running its own
    program. alice gives the say-so before asking; the two commands meant to
    fail are Jitney reading what it may not (Hoot's bid, then, having lost,
    the concierge's requests); and the last chapter ends on alice checking
    the whole chain."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse((TUTORIALS / "HOOT.md").read_text())
    steps = score["steps"]
    assert score["machines"] == ["alice", "concierge", "bank", "hoot", "jitney", "cab-17"]
    assert score["peers"] == {
        "alice": "rider.py",
        "concierge": "concierge.py",
        "bank": "bank.py",
        "hoot": "company.py",
        "jitney": "company.py",
        "cab-17": "cab.py",
    }
    assert [c["title"] for c in score["chapters"]] == [
        "The cast",
        "The ask and the bids",
        "The ride",
        "The chain",
    ]
    assert set(score["files"]) == {
        "peek.py",
        "rider.py",
        "ride.py",
        "concierge.py",
        "company.py",
        "bank.py",
        "cab.py",
        "custody.py",
    }
    runs = [s for s in steps if s["kind"] == "run"]
    allow = next(i for i, s in enumerate(runs) if "/allow" in s["cmd"])
    ask = next(i for i, s in enumerate(runs) if "/commit" in s["cmd"])
    assert allow < ask  # the say-so first, then the ride
    refused = [s for s in runs if s.get("fails")]
    assert [(s["machine"], s["cmd"].split()[-1]) for s in refused] == [
        ("jitney", "hoot"),
        ("jitney", "concierge"),
    ]
    last = score["chapters"][-1]["start"]
    assert runs[-1]["cmd"] == "python custody.py" and steps.index(runs[-1]) > last


def test_a_guide_can_show_a_machine_s_app():
    """`@demo app geese /app/` names a machine's own web app for the page to
    show as the browser in front of you; the machine must be in the cast."""
    md_manifest = _tool("md_manifest")
    score = md_manifest.parse("<!-- @demo machines geese -->\n<!-- @demo app geese /app/ -->\n")
    assert score["app"] == {"machine": "geese", "path": "/app/"}
    assert md_manifest.parse("<!-- @demo machines geese -->\n")["app"] is None
    for bad in ("<!-- @demo app bob /app/ -->", "<!-- @demo app geese app/ -->"):
        with pytest.raises(SystemExit):
            md_manifest.parse(f"<!-- @demo machines geese -->\n{bad}\n")
