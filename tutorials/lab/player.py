"""A player: this machine's backstage peer in the demo engine. Not part of the tutorial.

python player.py <domain>

Started instead of peer.py when the lab boots with `./lab.sh conductor`. It
runs peer.py as a child, and beside it a second, backstage peer of its own,
did:web:<machine>.stage%3A1337, on the stage network only. That peer follows
exactly one repo, the conductor's. Every cue the conductor signs and
addresses to this player, it performs, and it answers in its own repo:
what the command printed, how it ended, and what this machine sees.

This is remote execution authorized by a signature, and nothing more: a cue
runs because it verified as the conductor's. That is fine for a toy on your
own computer and nowhere else. A real deployment would put it behind a VPN's
ACLs, check each command against an allow-list (for example, its own copy of
the score), or not execute records at all. The players are yours to hack.
"""

import asyncio
import contextlib
import os
import re
import signal
import socket
import struct
import sys
from pathlib import Path

import httpx
import uvicorn
from backstage import Cue, Output, Packets, Result, Show, View, follow
from dids import resolver
from starlette.applications import Starlette

from fastatp import DIDServer, Identity, RepoMirror, build_did_document, datamodel
from fastatp.tid import generate_tid

domain = sys.argv[1]
machine = domain.split(".")[0]
CONDUCTOR = os.environ["CONDUCTOR"]  # the one DID this player takes cues from
CONDUCTOR_HOST = os.environ.get("CONDUCTOR_HOST", "conductor.stage")

# --- the backstage peer -----------------------------------------------------
# Made exactly like peer.py's identity, in its own state directory
# (state/backstage/<machine>.stage on your computer), so it survives a
# restart and goes with `./lab.sh clean`. Bootstrapping is awaited, so it
# happens in main(); these globals are what it leaves behind.
host = f"{machine}.stage"
did = f"did:web:{host}%3A1337"  # the port is part of the name


async def backstage_peer() -> Starlette:
    global identity, server, twin_slug, document, done
    identity = await Identity.bootstrap(domain=f"{host}%3A1337", state_dir=Path("/srv/backstage"))
    assert identity.did == did
    server = identity.create_server(resolver=resolver())
    twin_slug = identity.chain.twin_did.rsplit(":", 1)[-1]
    document = build_did_document(
        did=did,
        signing_key=identity.signer.public_key,
        also_known_as=[identity.chain.twin_did],
        pds_endpoint=f"http://{host}:1337",
    )
    done = answered()
    await close_interrupted()
    dids = DIDServer(domain=f"{host}%3A1337", resolver=did_resolver, chain_resolver=chain_resolver)
    return Starlette(routes=[*server.app.routes, *dids.app.routes])


async def did_resolver(slug: str):
    if slug == "":
        return document
    return identity.twin_document() if slug == twin_slug else None


async def chain_resolver(slug: str):
    return identity.chain.to_json() if slug == twin_slug else None


async def write(record, rkey: str | None = None) -> str:
    rkey = rkey or generate_tid()
    await server.put_record(record.nsid, rkey, record)
    return f"at://{did}/{record.nsid}/{rkey}"


# --- the tutorial's own process -----------------------------------------------

peer: asyncio.subprocess.Process | None = None
tasks: set[asyncio.Task] = set()  # background work this player keeps a reference to


async def run_peer() -> None:
    """peer.py as a child; its output goes to docker's log, as before, and /tmp/peer.log.

    PEER names this machine's program, as entrypoint.sh resolved it from the
    guide: peer.py, or the guide's own that builds on it (orchestra.py)."""
    global peer
    peer = await asyncio.create_subprocess_exec(
        "python",
        os.environ.get("PEER") or "peer.py",
        domain,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    with open("/tmp/peer.log", "a", encoding="utf-8") as log:
        async for raw in peer.stdout:
            line = raw.decode(errors="replace").rstrip()
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
    await peer.wait()


async def restart_peer() -> bool:
    """Stop peer.py and start it again; True once it answers, False if it never does."""
    # The stand-in for `./lab.sh restart alice`: peer.py stops and starts
    # again on the same disk. wg0 stays up, where a real reboot rebuilds it.
    if peer is not None and peer.returncode is None:
        peer.terminate()
        await peer.wait()
    tasks.add(asyncio.create_task(run_peer()))
    for _ in range(50):
        await asyncio.sleep(0.2)
        try:
            async with httpx.AsyncClient(timeout=1) as http:
                await http.get("http://127.0.0.1/whoami")
            return True
        except httpx.HTTPError:
            continue
    return False


# --- cues ---------------------------------------------------------------------

running: dict[str, asyncio.subprocess.Process] = {}
show: dict = {"id": None, "waiting": []}  # the conductor's current show, and cues seen before it


def answered() -> set[str]:
    """Every cue this player has already answered, read back from its own repo.

    A checkpoint after a reconnect, or a restart of this player, repeats cues
    already seen. The player's own results are the memory that makes acting
    on each cue exactly once survive both.
    """
    return cues_of(Result)


def cues_of(kind) -> set[str]:
    return {
        server.repo.get_record(kind.nsid, rkey)["cue"]
        for rkey, _ in server.repo.list_records(kind.nsid)
    }


async def close_interrupted() -> None:
    """Answer for every command this player had started when it last stopped.

    A command writes an Output record before it starts, its claim, and a
    Result when it ends. A claim with no result is a command a restart cut
    short. It may have taken effect, so it is never run again: it is answered
    as failed, and the conductor is not left waiting for it.
    """
    for uri in cues_of(Output) - done:
        await write(Output(cue=uri, lines=["[player] restarted while this ran; not run again"]))
        await write(Result(cue=uri, code=1))
        done.add(uri)


done: set[str] = set()  # filled by backstage_peer(), once the repo is open


async def on_cue(path: str, record: dict) -> None:
    if record.get("$type") == Show.nsid:
        show["id"] = record["id"]
        waiting, show["waiting"] = show["waiting"], []
        for p, r in waiting:
            await on_cue(p, r)
        return
    if record.get("$type") != Cue.nsid or record["to"] != did:
        return
    if show["id"] is None:
        show["waiting"].append((path, record))  # a checkpoint lists cues before the show record
        return
    uri = f"at://{CONDUCTOR}/{path}"
    if record["show"] != show["id"] or uri in done:
        return  # an earlier show's cue, or one already answered
    done.add(uri)
    try:
        if record["action"] == "run":
            tasks.add(asyncio.create_task(perform(uri, record)))
        elif record["action"] == "stop":
            for proc in list(running.values()):
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):  # it ended just now
                        os.killpg(
                            proc.pid, signal.SIGTERM
                        )  # Ctrl-C, for everything this machine left running
            await write(Result(cue=uri, code=0))
        elif record["action"] == "restart":
            back = await restart_peer()
            await write(Result(cue=uri, code=0 if back else 1))
    except Exception:
        done.discard(uri)  # not answered: the next checkpoint offers this cue again
        raise


async def perform(uri: str, cue: dict) -> None:
    buffer: list[str] = []
    stop = asyncio.Event()

    async def flush() -> None:
        # Take the lines first, then write them: a line that arrives during
        # the write waits for the next record instead of being lost.
        if buffer:
            lines, buffer[:] = buffer[:], []
            await write(Output(cue=uri, lines=lines))

    async def flush_every_so_often() -> None:
        # One record per line would be one commit per line. A few lines per
        # record is the same story in fewer commits: how fine to cut it is
        # the builder's choice.
        while not stop.is_set():
            await asyncio.sleep(0.3)
            await flush()

    code = 1  # what is reported if the command never ran, or this player tripped over it
    proc: asyncio.subprocess.Process | None = None
    flusher: asyncio.Task | None = None
    try:
        # The claim, before the command starts: an Output with no lines yet.
        # If this player dies from here on, close_interrupted() finds it.
        await write(Output(cue=uri, lines=[]))
        proc = await asyncio.create_subprocess_shell(
            cue["command"],
            cwd="/srv",
            env={**os.environ, **cue.get("env", {})},
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        running[uri] = proc
        flusher = asyncio.create_task(flush_every_so_often())

        async def pump() -> None:
            async for raw in proc.stdout:
                buffer.append(raw.decode(errors="replace").rstrip("\n"))

        try:
            await asyncio.wait_for(pump(), timeout=None if cue["bg"] else 60)
        except TimeoutError:
            os.killpg(proc.pid, signal.SIGTERM)
        code = await proc.wait()
        if code < 0:
            buffer.append("^C")
    except Exception as e:
        buffer.append(f"[player] could not run this: {e!r}")
    finally:
        # Whatever happened, the cue is answered: the conductor waits on the
        # Result and on nothing else. A command this player lost track of is
        # stopped first, so nothing is left running that no cue can reach.
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await proc.wait()
        running.pop(uri, None)
        stop.set()
        with contextlib.suppress(Exception):  # a failed write must not cost the Result
            if flusher is not None:
                await flusher  # let a write in flight finish; never cut a commit short
            await flush()
        await write(Result(cue=uri, code=code))


# --- what this machine sees ---------------------------------------------------


async def watch_view() -> None:
    """Its identity, its own public repo (verified, as any reader would), its tunnel peers."""
    last: str | None = None
    repo_view: dict = {}
    private: list = []  # this machine's private repos, as /mine last listed them
    seen_key = None
    async with httpx.AsyncClient(timeout=3) as http:
        while True:
            await asyncio.sleep(1)
            try:
                who = (await http.get("http://127.0.0.1/whoami")).json()
            except (httpx.HTTPError, ValueError):
                continue
            if (who["did"], who["seq"], who["channel"]) != seen_key:
                try:
                    car = (
                        await http.get(
                            "http://127.0.0.1/xrpc/com.atproto.sync.getRepo",
                            params={"did": who["did"]},
                        )
                    ).content
                    mirror = RepoMirror(who["did"], resolver=resolver())
                    await mirror.load_checkpoint(car)
                    records = []
                    for k in sorted(mirror.entries):
                        collection, rkey = k.split("/", 1)
                        records.append(
                            {
                                "path": k,
                                "cid": mirror.entries[k].encode(),
                                "value": datamodel.to_json(mirror.get_record(collection, rkey)),
                            }
                        )
                    repo_view = {"rev": mirror.rev, "records": records}
                    seen_key = (who["did"], who["seq"], who["channel"])
                except Exception:
                    continue
            # A program that keeps private repos lists them, to this machine
            # only, at /mine: its owner's view of them, shown like its terminal.
            # A poll that fails keeps the last good answer, so a hiccup never
            # blanks this machine's repos on the screen.
            with contextlib.suppress(httpx.HTTPError, ValueError):
                answer = await http.get("http://127.0.0.1/mine")
                if answer.status_code == 200 and isinstance(listed := answer.json(), list):
                    private = listed
            # Three short shell commands, run on a thread so nothing else this
            # player does waits for them.
            key, ip, peers = await asyncio.to_thread(
                lambda: (wg_public_key(), lan_ip(), wg_peers())
            )
            view = View(
                identity={**who, "wg_public_key": key, "lan_ip": ip},
                repo=repo_view,
                wg=peers,
                private=private,
            )
            snapshot = view.model_dump_json()
            if snapshot != last:
                last = snapshot
                await write(
                    view, rkey="self"
                )  # one record, updated in place: the present, not a history


def wg_dump() -> list[list[str]]:
    return [
        line.split("\t")
        for line in os.popen("wg show wg0 dump 2>/dev/null").read().strip().splitlines()
    ]


def wg_public_key() -> str:
    rows = wg_dump()
    # The first row is the interface: private key, public key, port, fwmark.
    # Only the public key ever leaves this function.
    return rows[0][1] if rows else ""


def wg_peers() -> list[dict]:
    return [
        {
            "public_key": r[0],
            "endpoint": r[2],
            "allowed_ips": r[3],
            "latest_handshake": int(r[4]),
            "rx": int(r[5]),
            "tx": int(r[6]),
        }
        for r in wg_dump()[1:]
    ]


def lan_address() -> tuple[str, str]:
    for line in os.popen("ip -o -4 addr show").read().splitlines():
        if m := re.search(r"^\d+: (\S+)\s+inet (192\.168\.77\.\d+)/", line):
            return m.group(1).split("@")[0], m.group(2)
    return "eth0", ""


def lan_ip() -> str:
    return lan_address()[1]


async def watch_wire() -> None:
    """Every packet this machine sends on the LAN (-Q out), so together the players show each packet once."""
    pending: list[dict] = []

    async def publish():
        while True:
            await asyncio.sleep(1)
            if pending:
                # taken before the write, so a packet seen during it is kept
                batch, pending[:] = pending[:], []
                await write(Packets(packets=batch))

    tasks.add(asyncio.create_task(publish()))
    try:
        proc = await asyncio.create_subprocess_exec(
            "tcpdump",
            "-i",
            lan_address()[0],
            "-Q",
            "out",
            "-U",
            "-s",
            "600",
            "-w",
            "-",
            "tcp port 80 or udp port 51820",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        read = proc.stdout.readexactly
        await read(24)  # pcap file header
        while True:
            sec, usec, caplen, _ = struct.unpack("<IIII", await read(16))
            if pkt := summarize(await read(caplen)):
                pending.append({"at_ms": sec * 1000 + usec // 1000, **pkt})
    except Exception as e:
        print(f"[player] wire capture stopped: {e!r}", flush=True)


def summarize(frame: bytes) -> dict | None:
    if len(frame) < 34 or frame[12:14] != b"\x08\x00":
        return None
    ip = frame[14:]
    seg = ip[(ip[0] & 0x0F) * 4 :]
    src, dst = ".".join(map(str, ip[12:16])), ".".join(map(str, ip[16:20]))
    if ip[9] == 17:  # UDP: WireGuard
        payload = seg[8:]
        kinds = {
            1: "handshake initiation",
            2: "handshake response",
            3: "cookie",
            4: "transport data",
        }
        return {
            "src": src,
            "dst": dst,
            "proto": "wg",
            "len": len(payload),
            "kind": kinds.get(payload[0] if payload else 0, "?"),
            "text": "",
        }
    if ip[9] == 6:  # TCP: plain http on port 80
        payload = seg[(seg[12] >> 4) * 4 :]
        if not payload:
            return None  # a bare ACK or SYN says nothing an observer cares about
        head = payload[:400]
        first = head.split(b"\r\n", 1)[0].decode(errors="replace")
        kind = first if re.match(r"^(GET|POST|HTTP/1\.1) ", first) else f"{len(payload)} bytes"
        readable = " ".join(m.decode() for m in re.findall(rb"[ -~]{5,}", head))
        return {
            "src": src,
            "dst": dst,
            "proto": "http",
            "len": len(payload),
            "kind": kind,
            "text": readable[:240],
        }
    return None


async def main() -> None:
    app = await backstage_peer()
    # Listen on the stage network only: the LAN never carries backstage traffic.
    stage_ip = socket.gethostbyname(host)
    backstage = uvicorn.Server(uvicorn.Config(app, host=stage_ip, port=1337, log_level="warning"))
    print(f"[player] {did} on {stage_ip}:1337, taking cues from {CONDUCTOR}", flush=True)
    await asyncio.gather(
        backstage.serve(),
        run_peer(),
        watch_view(),
        watch_wire(),
        follow(CONDUCTOR, CONDUCTOR_HOST, on_cue),
    )


asyncio.run(main())
