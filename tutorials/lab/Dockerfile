# One image, three machines. Nothing in here is fastatp-specific beyond installing fastatp.
# Built from the fastatp repo root, so the machines run the fastatp in this tree, the
# exact commit the guide is tested against, and the demo engine can carry the guides.
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
COPY tutorials/conductor/conductor.py tutorials/tools/md_manifest.py tutorials/tools/play.py tutorials/GETTING_STARTED.md tutorials/CONDUCTOR_AND_ORCHESTRA.md tutorials/HOOT.md ./
COPY tutorials/sketches ./sketches
RUN python md_manifest.py GETTING_STARTED.md > GETTING_STARTED.json
RUN python md_manifest.py CONDUCTOR_AND_ORCHESTRA.md > CONDUCTOR_AND_ORCHESTRA.json
RUN python md_manifest.py HOOT.md > HOOT.json
COPY --from=page /page/dist ./page/
ENTRYPOINT ["python", "conductor.py"]
