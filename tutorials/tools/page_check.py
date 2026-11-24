"""Look at the conductor's page the way a viewer does, and say what is broken.

python page_check.py [http://localhost:8080]     (run it on your computer, after a play)

`play.py` proves the guide's commands run. This proves the page that shows
them holds together. It opens the page in a headless Chrome, then presses
Back through every step the show has played, and at each one checks that:

- the page raised no error and logged none;
- the page is no wider than the window (a card that pushes it wider hides
  the buttons);
- a key concept shows its card, with its picture if the guide gives it one,
  and no card is up for a step that is not a key concept.

Before that, it checks that the chapter menu lists the chapters the score
has. Looking back never touches the machines, so this is safe on a show
that is still wanted. It exits non-zero and lists what it found.

Needs a Chrome or Chromium on the path and the `websockets` package, which
fastatp already depends on.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

VISIBLE = (
    "run",
    "restart",
    "stop",
    "commit",
    "say",
    "concept",
    "chapter",
    "ask",
)  # one press of Next each
WIDTH, HEIGHT = 1500, 1000

CARD = "document.querySelector('.gotit')?.closest('.card')"


class Page:
    """One tab, driven over Chrome's debugging socket."""

    def __init__(self, socket) -> None:
        self.socket = socket
        self.sent = 0
        self.errors: list[str] = []

    async def call(self, method: str, **params) -> dict:
        self.sent += 1
        mine = self.sent
        await self.socket.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            # bounded, so a page that stops answering fails the check instead of hanging it
            message = json.loads(await asyncio.wait_for(self.socket.recv(), timeout=10))
            if message.get("id") == mine:
                if "error" in message:  # Chrome refused the request: say what it said
                    raise RuntimeError(f"{method}: {message['error'].get('message', message)}")
                return message.get("result", {})
            self.note(message)

    def note(self, message: dict) -> None:
        """Keep what the page threw or logged as an error while we were waiting."""
        params = message.get("params", {})
        if message.get("method") == "Runtime.exceptionThrown":
            details = params["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text", "")
            self.errors.append(text.splitlines()[0][:200])
        elif message.get("method") == "Log.entryAdded" and params["entry"]["level"] == "error":
            self.errors.append(params["entry"]["text"][:200])
        elif message.get("method") == "Runtime.consoleAPICalled" and params["type"] == "error":
            said = " ".join(str(a.get("value", a.get("description", ""))) for a in params["args"])
            self.errors.append(said[:200])

    async def js(self, expression: str):
        result = await self.call(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True
        )
        return result.get("result", {}).get("value")

    async def until(self, expression: str, seconds: float = 4) -> bool:
        """Wait for something on the page to become true; False if it never does."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if await self.js(expression):
                return True
            await asyncio.sleep(0.05)
        return False


def plain(markdown: str) -> str:
    """A title as the page shows it: the guide's `code` and *emphasis* marks gone."""
    return " ".join(markdown.replace("`", "").replace("*", "").split())


async def check(page: Page, url: str) -> list[str]:
    found: list[str] = []
    with urllib.request.urlopen(f"{url}/manifest.json", timeout=10) as response:
        score = json.load(response)

    await page.call("Page.enable")
    await page.call("Runtime.enable")
    await page.call("Log.enable")
    await page.call("Page.navigate", url=url)

    # The menu opens the page: one card per chapter of the score.
    wanted = len(score["chapters"])
    cards = "document.querySelectorAll('.overlay .cards button.card').length"
    if not await page.until(f"{cards} === {wanted}", seconds=15):
        found.append(f"the menu shows {await page.js(cards)} chapters; the score has {wanted}")
        return found + [f"page error: {e}" for e in page.errors]
    step = await page.js(
        "Number((document.querySelector('.progress')?.title.match(/step (\\d+)/) || [])[1])"
    )
    played = [s for s in score["steps"][: int(step or 0)] if s["kind"] in VISIBLE]
    if len(played) < 2:
        return [*found, "the show has not been played: play it first, then check the page"]
    await page.js("document.querySelector('.overlay .close').click()")
    await page.until("!document.querySelector('.overlay .cards')")

    # Now back through everything the show played, newest first.
    back = "document.querySelector('.controls button')"
    cards_seen = pictures_seen = visited = 0
    for behind, shown in enumerate(reversed(played)):
        where = f"step {shown['id']} ({shown['kind']})"
        if behind:
            if await page.js(f"{back}.disabled"):
                found.append(f"{where}: Back is disabled with {len(played) - behind} steps to go")
                break
            await page.js(f"{back}.click()")
        visited += 1
        if not behind:
            pass  # where the show stands: a key concept here was answered, so its card is down
        elif shown["kind"] == "concept":
            # the card for this concept, not one left over from another: its heading says which
            title = json.dumps(plain(shown["title"]))
            heading = f"{CARD}?.querySelector('h2')?.textContent.replace(/\\s+/g, ' ').trim()"
            if not await page.until(f"!!{CARD}"):
                found.append(f"{where}: no card for the key concept {shown['title']!r}")
            elif not await page.until(f"{heading} === {title}"):
                found.append(
                    f"{where}: the card is headed {await page.js(heading)!r},"
                    f" and the guide's key concept is {shown['title']!r}"
                )
            elif shown.get("sketch") and not await page.until(f"!!{CARD}.querySelector('svg')"):
                found.append(f"{where}: the card has no picture ({shown['sketch']})")
            else:
                cards_seen += 1
                pictures_seen += bool(shown.get("sketch"))
        elif not await page.until(f"!{CARD}", seconds=2):
            found.append(f"{where}: a key concept's card is up, and this is not a key concept")
        wide = await page.js("document.documentElement.scrollWidth")
        if wide > WIDTH:
            found.append(f"{where}: the page is {wide} wide in a window of {WIDTH}")
        for error in page.errors:
            found.append(f"{where}: page error: {error}")
        page.errors.clear()
    print(
        f"looked back through {visited} of {len(played)} steps of {score['source']}:"
        f" {wanted} chapters,"
        f" {cards_seen} key concept cards, {pictures_seen} with a picture"
    )
    return found


async def main(url: str) -> int:
    chrome = next(
        (c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None
    )
    if chrome is None:
        print("no Chrome or Chromium found; the page was not checked")
        return 1
    # Chrome can still be writing its profile as it exits; a leftover file is not our failure
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile,
        tempfile.TemporaryFile() as log,
    ):
        browser = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",  # a container or CI runner may have a small /dev/shm
                "--remote-debugging-port=0",  # any free port; Chrome writes which one below
                f"--user-data-dir={profile}",
                f"--window-size={WIDTH},{HEIGHT}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            for _ in range(600):  # up to a minute: a first start on a cold machine is slow
                if port_file.exists() and port_file.read_text().strip():
                    break
                if browser.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            if not (port_file.exists() and port_file.read_text().strip()):
                ended = browser.poll()
                why = f"exited with {ended}" if ended is not None else "wrote no debugging port"
                print(f"{chrome} did not start ({why}); the page was not checked. It said:")
                log.seek(0)
                print(log.read().decode(errors="replace")[-2000:] or "(nothing)")
                return 1
            port = port_file.read_text().split()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as response:
                tab = next(t for t in json.load(response) if t["type"] == "page")
            async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None) as socket:
                try:
                    found = await check(Page(socket), url.rstrip("/"))
                except (TimeoutError, RuntimeError) as e:
                    found = [f"the check could not finish: {e or 'the page stopped answering'}"]
        finally:
            browser.terminate()
            browser.wait()
    for line in found:
        print(f"WRONG: {line}")
    print("the page holds together" if not found else f"{len(found)} things wrong with the page")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080")))
