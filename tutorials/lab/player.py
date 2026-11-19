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
    """peer.py as a child; its output goes to docker's log, as before, and /tmp/peer.log."""
    global peer
    peer = await asyncio.create_subprocess_exec(
        "python",
        "peer.py",
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


async def restart_peer() -> None:
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
            return
        except httpx.HTTPError:
            continue


# --- cues ---------------------------------------------------------------------

running: dict[str, asyncio.subprocess.Process] = {}
show: dict = {"id": None, "waiting": []}  # the conductor's current show, and cues seen before it


def answered() -> set[str]:
    """Every cue this player has already answered, read back from its own repo.

    A checkpoint after a reconnect, or a restart of this player, repeats cues
    already seen. The player's own results are the memory that makes acting
    on each cue exactly once survive both.
    """
    return {
        server.repo.get_record(Result.nsid, rkey)["cue"]
        for rkey, _ in server.repo.list_records(Result.nsid)
    }


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
    if record["action"] == "run":
        tasks.add(asyncio.create_task(perform(uri, record)))
    elif record["action"] == "stop":
        for proc in list(running.values()):
            if proc.returncode is None:
                os.killpg(
                    proc.pid, signal.SIGTERM
                )  # Ctrl-C, for everything this machine left running
        await write(Result(cue=uri, code=0))
    elif record["action"] == "restart":
        await restart_peer()
        await write(Result(cue=uri, code=0))


async def perform(uri: str, cue: dict) -> None:
    proc = await asyncio.create_subprocess_shell(
        cue["command"],
        cwd="/srv",
        env={**os.environ, **cue.get("env", {})},
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    running[uri] = proc
    buffer: list[str] = []

    async def flush_every_so_often():
        # One record per line would be one commit per line. A few lines per
        # record is the same story in fewer commits: how fine to cut it is
        # the builder's choice.
        while True:
            await asyncio.sleep(0.3)
            if buffer:
                await write(Output(cue=uri, lines=buffer[:]))
                buffer.clear()

    flusher = asyncio.create_task(flush_every_so_often())
    try:

        async def pump():
            async for raw in proc.stdout:
                buffer.append(raw.decode(errors="replace").rstrip("\n"))

        await asyncio.wait_for(pump(), timeout=None if cue["bg"] else 60)
    except TimeoutError:
        os.killpg(proc.pid, signal.SIGTERM)
    code = await proc.wait()
    flusher.cancel()
    if code < 0:
        buffer.append("^C")
    if buffer:
        await write(Output(cue=uri, lines=buffer[:]))
    await write(Result(cue=uri, code=code))
    running.pop(uri, None)


# --- what this machine sees ---------------------------------------------------


async def watch_view() -> None:
    """Its identity, its own public repo (verified, as any reader would), its tunnel peers."""
    last: str | None = None
    repo_view: dict = {}
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
            view = View(
                identity={**who, "wg_public_key": wg_public_key(), "lan_ip": lan_ip()},
                repo=repo_view,
                wg=wg_peers(),
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
                await write(Packets(packets=pending[:]))
                pending.clear()

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
