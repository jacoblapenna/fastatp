#!/usr/bin/env bash
# The whole docker surface of this tutorial:
#   ./lab.sh up [machine...]     build the image and boot alice, bob and eve, or the machines named
#   ./lab.sh conductor [SCORE]   boot a guide's cast with the demo engine playing it, its page on http://localhost:8080
#   ./lab.sh test [SCORE]        clean, boot the demo engine, play a guide with nobody watching, report (AGENTS.md)
#   ./lab.sh logs alice          what alice's process prints (Ctrl-C to stop watching)
#   ./lab.sh alice <command...>  run a command on alice's machine (curl, python follow.py, wg show, ...)
#   ./lab.sh restart alice       reboot alice's machine
#   ./lab.sh tap <tcpdump args>  watch the LAN from outside (optional); -w /state/x.pcap saves to state/x.pcap
#   ./lab.sh down                stop the machines; state survives
#   ./lab.sh clean               stop the machines and delete all state
set -euo pipefail
cd "$(dirname "$0")"
self="$PWD/$(basename "$0")"   # this script by its whole path, so it can call itself from here
# a guide's name, checked before anything is built or cleaned: a typo stops here, not in the conductor
guide() { [ -f "../$1.md" ] || { echo "no guide called $1: give its name without .md, like CONDUCTOR_AND_ORCHESTRA" >&2; exit 1; }; }
MACHINES="alice bob eve concierge bank hoot jitney cab-17 geese"   # every machine compose.yml knows
# a cue's words from the guide, one line per cue, read with sed so the host needs no Python
cue() { sed -n "s/^<!-- @demo $1 \(.*\) -->$/\1/p" "../$SCORE.md"; }
machines() { for m in "$@"; do case " $MACHINES " in *" $m "*) ;; *) echo "no machine called $m: the lab has $MACHINES" >&2; exit 1 ;; esac; done; }
case "${1:-}" in
  up)      mkdir -p state; cast=("${@:2}"); [ ${#cast[@]} -gt 0 ] || cast=(alice bob eve); machines "${cast[@]}"
           docker compose up --build -d --wait "${cast[@]}" ;;
  conductor) mkdir -p state; export SCORE="${2:-${SCORE:-GETTING_STARTED}}"
           guide "$SCORE"
           # the guide's cast boots, and only it (<!-- @demo machines alice bob eve -->)
           read -ra cast <<< "$(cue machines | head -1)"; [ ${#cast[@]} -gt 0 ] || cast=(alice bob eve); machines "${cast[@]}"
           # the program each machine runs: one for all (<!-- @demo peer orchestra.py -->), one for
           # a machine (<!-- @demo peer bank bank.py -->, passed on as bank=bank.py), or peer.py
           export PEER="${PEER:-$(cue peer | sed 's/^\([a-z0-9-]*\) /\1=/' | tr '\n' ' ')}"
           # machines left over from another guide's cast stop, so the cast is exactly this one
           others=(); for m in $MACHINES; do case " ${cast[*]} " in *" $m "*) ;; *) others+=("$m") ;; esac; done
           docker compose --profile '*' rm -sf "${others[@]}" >/dev/null 2>&1 || true
           CONDUCTOR=did:web:conductor.stage docker compose --profile conductor up --build -d --wait conductor "${cast[@]}"
           echo "the conductor is up: open http://localhost:8080" ;;
  test)    export SCORE="${2:-GETTING_STARTED}"; guide "$SCORE"; "$self" clean >/dev/null 2>&1; "$self" conductor >/dev/null
           # a guide with an app has turns the viewer takes in it: hands.py takes them, in a
           # headless Chrome on this computer, where the page is (<!-- @demo you ... -->)
           hands=""; if [ -n "$(cue app)" ]; then
             if command -v uv >/dev/null; then uv run --no-sync python ../tools/hands.py & else python3 ../tools/hands.py & fi
             hands=$!
           fi
           code=0; docker compose exec -T conductor python play.py --speed "${SPEED:-4}" || code=$?
           if [ -n "$hands" ]; then
             if [ "$code" = 0 ]; then wait "$hands" || code=$?; else kill "$hands" 2>/dev/null || true; fi
           fi
           exit "$code" ;;
  down)    docker compose --profile '*' down ;;
  clean)   docker compose --profile '*' down; docker run --rm -v "$PWD/state:/state" alpine:3.20 sh -c 'rm -rf /state/*' ;;
  logs)    docker compose logs -f --no-log-prefix "${2:?machine}" ;;
  restart) docker compose restart "${2:?machine}" ;;
  tap)     docker compose run --rm tap "${@:2}" ;;
  alice|bob|eve|concierge|bank|hoot|jitney|cab-17|geese) if [ -t 0 ]; then docker compose exec "$@"; else docker compose exec -T "$@"; fi ;;
  *) sed -n '2,11p' "$self"; exit 1 ;;
esac
