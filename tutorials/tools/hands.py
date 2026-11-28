"""Do the viewer's turns in the app, so a guide with an app plays with nobody watching.

python hands.py [http://localhost:8080]    (run it on your computer, beside play.py)

A guide that shows a machine's web app (`<!-- @demo app geese /app/ -->`)
has turns: a paragraph that tells the viewer what to do in the app, and a
`<!-- @demo you ... -->` cue under it naming the request that says it is
done. The show waits for that request. `play.py` presses Play and checks
the rest; this is the viewer's hands. It opens the conductor's page in a
headless Chrome, the way a viewer does, and at each turn does what the
guide's `<!-- @demo do ... -->` cues say, in the app's own frame: it clicks,
ticks, copies a value from one device to the other, and reloads, as a
person would. It never tells the conductor anything: the app's request,
through the page, is what moves the show on.

The second device is the same app on the other name for this computer, a
different site to Chrome, which runs it in a process of its own. That
keeps its storage apart, as a second device's is, and also out of reach
of a driver on the first frame's process; Chrome is started with site
isolation off here so one driver can reach both frames. The storage stays
apart: it belongs to the origin, not the process.

It ends when the show does. Needs a Chrome or Chromium on the path.
"""

import asyncio
import json
import sys

import websockets
from page_check import NoChrome, Page, chrome

FRAME = "document.querySelector('iframe[data-device={device}]')"
SHOWING = "[...document.querySelectorAll('section')].some(s => !s.hidden)"  # the app has started


class Hands:
    def __init__(self, page: Page) -> None:
        self.page = page

    async def context(self, device: str) -> int:
        """A place to run JavaScript inside one device's frame, as it is now."""
        for _ in range(100):
            src = await self.page.js(f"{FRAME.format(device=device)}?.src ?? ''")
            if src:
                tree = await self.page.call("Page.getFrameTree")
                for child in tree["frameTree"].get("childFrames", []):
                    if child["frame"]["url"] == src:
                        world = await self.page.call(
                            "Page.createIsolatedWorld",
                            frameId=child["frame"]["id"],
                            worldName="hands",
                        )
                        return world["executionContextId"]
            await asyncio.sleep(0.1)  # the panel is not up yet, or the frame is still loading
        raise RuntimeError(f"no frame for {device} on the page")

    async def js(self, device: str, expression: str):
        result = await self.page.call(
            "Runtime.evaluate",
            expression=expression,
            contextId=await self.context(device),
            returnByValue=True,
            awaitPromise=True,
        )
        return result.get("result", {}).get("value")

    async def until(self, device: str, expression: str, what: str, seconds: float = 20) -> None:
        for _ in range(int(seconds * 10)):
            try:
                if await self.js(device, expression):
                    return
            except RuntimeError:
                pass  # the frame is between pages
            await asyncio.sleep(0.1)
        try:
            showing = await self.js(device, "document.querySelector('section:not([hidden])')?.id")
        except RuntimeError:
            showing = None
        errors = "; ".join(self.page.errors[-3:]) or "none"
        raise RuntimeError(
            f"{what}, on {device}, never happened (the app shows {showing or 'nothing'};"
            f" page errors: {errors})"
        )

    async def do(self, action: dict) -> None:
        device, verb, args = action["device"], action["action"], action["args"]
        if verb == "reload":
            await self.page.js(
                f"document.querySelector('[data-anchor={device}] button.reload').click()"
            )
            await asyncio.sleep(0.5)
            await self.until(device, SHOWING, "the app starting again")
            return
        await self.until(device, SHOWING, "the app starting")
        target = f"document.querySelector({json.dumps(args[0])})"
        # what a person can press: on the page, showing, and not greyed out
        ready = f"(e => e && e.offsetParent !== null && !e.disabled)({target})"
        await self.until(device, ready, f"{args[0]} showing")
        if verb == "click":
            await self.js(device, f"{target}.click()")
        elif verb == "tick":
            await self.js(device, f"{target}.checked || {target}.click()")
        elif verb == "copy":
            source = f"document.querySelector({json.dumps(args[2])})"
            await self.until(args[1], f"!!{source}?.textContent.trim()", f"{args[2]} filled in")
            value = await self.js(args[1], f"{source}.textContent.trim()")
            await self.js(device, f"{target}.value = {json.dumps(value)}")


async def play(page: Page, url: str) -> int:
    socket_url = url.replace("http", "ws", 1) + "/ws"
    for _ in range(60):  # the conductor may still be starting
        try:
            ws = await websockets.connect(socket_url, max_size=None)
            break
        except (OSError, websockets.exceptions.InvalidMessage):
            await asyncio.sleep(0.5)
    else:
        print(f"hands: no conductor at {socket_url}")
        return 1
    for domain in ("Page", "Runtime", "Log"):  # Runtime and Log: errors, to say what went wrong
        await page.call(f"{domain}.enable")
    await page.call("Page.navigate", url=url)
    hands = Hands(page)
    done: set[int] = set()
    async with ws:
        hello = json.loads(await ws.recv())
        steps = hello["manifest"]["steps"]
        message = hello
        while True:
            if message.get("type") in ("hello", "status"):
                if message["done"] and message.get("you") is None and message["cursor"] >= 0:
                    print(f"hands: {len(done)} turns done in the app")
                    return 0
                turn = message.get("you")
                if turn is not None and turn not in done:
                    done.add(turn)
                    step = steps[turn]
                    print(f"hands: step {turn}: {step['text'][:70]}")
                    try:
                        for action in step["do"]:
                            await hands.do(action)
                    except RuntimeError as e:
                        print(f"hands: step {turn}: {e}")
                        return 1
            message = json.loads(await ws.recv())


async def main(url: str) -> int:
    try:
        # one driver for both devices' frames: see the docstring
        isolation = (
            "--disable-site-isolation-trials",
            "--disable-features=IsolateOrigins,site-per-process",
        )
        async with chrome(*isolation) as page:
            return await play(page, url.rstrip("/"))
    except NoChrome as e:
        print(f"hands: {e}; nobody can take the viewer's turns")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080")))
