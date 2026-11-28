# One image, every machine. Nothing in here is fastatp-specific beyond installing fastatp.
# Built from the fastatp repo root, so the machines run the fastatp in this tree, the
# exact commit the guide is tested against, and the demo engine can carry the guides.

# The browser peer: fastatp's Rust core compiled to WebAssembly, from this tree,
# with the same pinned tools CI and the release use (scripts/wasm-tools.env,
# hash-checked). A pip install of a release gets this bundle inside the wheel;
# the lab builds it, so the browser runs this commit too and the reader needs
# Docker and nothing else. The tools are published for x86_64, and the bundle
# is the same bytes on any machine, so this one stage is built as amd64 (on an
# arm64 computer, Docker emulates it).
FROM --platform=linux/amd64 rust:1.99.1-slim-bookworm AS wasm
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY rust-toolchain.toml pyproject.toml ./
COPY scripts/wasm-tools.env scripts/install-wasm-tools.sh scripts/build-wasm.sh ./scripts/
COPY rust/Cargo.lock ./rust/
RUN scripts/install-wasm-tools.sh /opt/wasm-tools   # checks wasm-bindgen against Cargo.lock
COPY rust ./rust
# The crates downloaded and compiled are kept between builds (cache mounts,
# never in the image), so a change to pyproject.toml or rust/ rebuilds only
# what it touched; the bundle the next stage copies is written outside them.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/rust/target \
    mkdir -p fastatp/browser && PATH="/opt/wasm-tools:$PATH" scripts/build-wasm.sh

FROM python:3.12-slim AS machine
RUN apt-get update \
 && apt-get install -y --no-install-recommends wireguard-tools iproute2 iputils-ping curl tcpdump procps \
 && rm -rf /var/lib/apt/lists/*
# fastatp's runtime dependencies exactly as the repo's uv.lock pins them, hashes
# checked; then fastatp itself from the tree. No version is named anywhere.
# The dependencies are their own layer, so editing fastatp's source rebuilds
# only the last step and downloads nothing again.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
COPY pyproject.toml uv.lock /src/
RUN cd /src && uv export --frozen --no-dev --no-emit-project --format requirements-txt --no-header > requirements.txt \
 && uv pip install --system --require-hashes -r requirements.txt
COPY README.md /src/
COPY fastatp /src/fastatp
COPY --from=wasm /src/fastatp/browser/static /src/fastatp/browser/static
RUN cd /src && uv pip install --system --no-deps . && rm -rf /src
ENV PYTHONUNBUFFERED=1
WORKDIR /srv
COPY tutorials/lab/entrypoint.sh tutorials/lab/lexicons.py tutorials/lab/dids.py tutorials/lab/peer.py \
     tutorials/lab/pair.py tutorials/lab/follow.py tutorials/lab/tail.py tutorials/lab/verify.py tutorials/lab/blocks.py ./
# Backstage, for `./lab.sh conductor` only: this machine's player in the demo engine.
COPY tutorials/lab/backstage.py tutorials/lab/player.py ./
COPY tutorials/lab/music.py tutorials/lab/orchestra.py tutorials/lab/trace.py tutorials/lab/replay.py ./
COPY tutorials/lab/ride.py tutorials/lab/rider.py tutorials/lab/concierge.py tutorials/lab/bank.py \
     tutorials/lab/company.py tutorials/lab/cab.py tutorials/lab/custody.py tutorials/lab/peek.py ./
COPY tutorials/lab/geese.py ./
COPY tutorials/lab/geese ./geese
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]

# The conductor's page, built once; `./lab.sh up` never builds this stage.
FROM node:22.23.4-slim AS page
WORKDIR /page
COPY tutorials/conductor/package.json tutorials/conductor/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tutorials/conductor/index.html tutorials/conductor/vite.config.js ./
COPY tutorials/conductor/src ./src
COPY tutorials/conductor/public ./public
RUN npx vite build

# The conductor: a peer of its own, the scores built from the guides, and the page.
FROM machine AS conductor
COPY tutorials/conductor/conductor.py tutorials/tools/md_manifest.py tutorials/tools/play.py tutorials/GETTING_STARTED.md tutorials/CONDUCTOR_AND_ORCHESTRA.md tutorials/HOOT.md tutorials/GEESE.md ./
COPY tutorials/sketches ./sketches
RUN python md_manifest.py GETTING_STARTED.md > GETTING_STARTED.json
RUN python md_manifest.py CONDUCTOR_AND_ORCHESTRA.md > CONDUCTOR_AND_ORCHESTRA.json
RUN python md_manifest.py HOOT.md > HOOT.json
RUN python md_manifest.py GEESE.md > GEESE.json
COPY --from=page /page/dist ./page/
ENTRYPOINT ["python", "conductor.py"]
