# Tutorials

These tutorials are better run locally, in Docker. The demo engine actually
uses FastATP to orchestrate the demo workload.

As code becomes cheap, tutorials need to adapt to the system engineer
rather than the person writing code. The demo engine, played in your
browser by the conductor of each tutorial's orchestra, shows the score and
the key concepts as they come up.

While you can type along with the README, it's highly encouraged to clone
the repo and run the conductor for the tutorial you want to watch. They
build on each other, so it's best to start at the beginning and go through
them.

Your agent can even run them and summarize the results: point it at
[the page for coding agents](../../tutorials/AGENTS.md).

## Watch one

You need git and Docker with the compose plugin.

```bash
git clone https://github.com/jacoblapenna/fastatp
cd fastatp/tutorials/lab
./lab.sh conductor GETTING_STARTED
```

Then open <http://localhost:8080> and press **Start**. `./lab.sh clean`
removes everything when you are done.

## The guides

<!-- guides -->

[How the tutorials run](../../tutorials/README.md), every way to play
them, and how to write one.
