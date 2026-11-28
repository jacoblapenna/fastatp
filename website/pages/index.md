<h1>
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="../../brand/lockup-light.svg">
    <img src="../../brand/lockup-dark.svg" alt="FastATP" width="420">
  </picture>
</h1>

FastATP is a composable atproto personal data server, run as a Python
library, that owns its private key and never shares it.

The future I want looks less like browsing and more like asynchronous,
distributed workloads; cryptographically provable chains of custody; and
humans in the mix where humans want to still be in the mix. I believe:

**Secrets should be kept, not shared.** A FastATP peer signs with a key
that never leaves it. You can bolt OAuth onto it, but it was built so that
nothing secret ever has to cross the wire.

**Authenticity will be the coin when content and code are cheap.** People
increasingly reach information through models and agents rather than
through pages they click. As content and code generation get even cheaper,
authenticity and proof-of-origin become even more valuable. Every FastATP
record is signed, so anyone can check where it came from, with hops composing across peers.

**Data, not calls.** This is my alternative to MCP. As workloads become
more distributed, longer-running, and more asynchronous, signed data will
become the grammar. There are no
calls here. There are only commits to your own repo, and the peers that follow
it. What to do with the data in a lexicon can be as rich or basic as the peer you build requires.

**Honest expression requires sovereignty over privacy.** A private connection
is setup from both sides: one party has to knock, and the owner has to admit them. Access
is granted by publishing a capability record and revoked by deleting it.
Privacy comes from who can reach a repo, not from a password crossing the
wire in exchange for a token that crosses for each call thereafter.

**"Good" behavior is best achieved with composable incentives rather than blanket rules and policies.** On this web, good behavior emerges from
incentives, and from a market composed on the authenticity of records. Much like collision avoidance emerges during rush hour in your
city without any one driver steering every car. Every driver/peer has a stake,
can compose their own risk tolerance, and can choose to trust or ignore
the signals of other drivers/peers.

I believe atproto is a foundation for one possible solution. FastATP is an experiment on how we get there from atproto. It keeps
atproto's bytes on the wire, drops the login, and puts private repos
behind WireGuard. This is just my answer to a future I want. Adoption by
the community is what decides whether it is the future.

If you're interested in learning more, [start with the
tutorials](#start-with-the-tutorials), or [hand it to your
agent](#hand-it-to-your-agent). If you want to dive right in, check out
[the README, the philosophy, and the GitHub repo](#dive-in). To hear more
about my vision of the future, [read my writing](#writing). To learn about
the interesting and technical sides of FastATP and atproto, [check out the
resources](#resources).

## Start with the tutorials

Watch FastATP peers run on your own machine, in Docker, played step by step
in your browser by a demo engine that is itself built on FastATP.

```bash
git clone https://github.com/jacoblapenna/fastatp
cd fastatp/tutorials/lab
./lab.sh conductor GETTING_STARTED
```

[All the tutorials](tutorials.md), in order.

## Hand it to your agent

Most people will meet this library through a coding agent, so the site is
built for one. Give yours either of these:

- <https://fastatp.com/llms.txt>: every page on this site, each with a
  markdown copy.
- [The page for coding agents](../../tutorials/AGENTS.md): how an agent
  runs a tutorial by itself, in about five minutes, and answers from what
  the machines did.

## Dive in

- [The README](../../README.md): what FastATP does, and how to install it.
- [The philosophy](../../PHILOSOPHY.md): the seven tenets, and what they
  forbid.
- [The GitHub repo](https://github.com/jacoblapenna/fastatp): the code,
  the issues, and the changelog.

## Writing

Under construction. Essays on the beliefs above are on their way: why
authenticity gains value as code gets cheap, a browser that is a peer and
renders the lexicons it chooses, and how good behavior can emerge from
incentives.

## Resources

Under construction. Interactive explainers of FastATP and atproto are on
their way, starting with how a repo's tree keeps its records in order.
