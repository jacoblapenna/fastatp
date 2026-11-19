#!/usr/bin/env bash
# The whole docker surface of this tutorial:
#   ./lab.sh up                  build the image and boot alice, bob and eve
#   ./lab.sh conductor           Part 4: boot them with the demo engine, its page on http://localhost:8080
#   ./lab.sh test [SCORE]        clean, boot the demo engine, play a guide with nobody watching, report (AGENTS.md)
#   ./lab.sh logs alice          what alice's process prints (Ctrl-C to stop watching)
#   ./lab.sh alice <command...>  run a command on alice's machine (curl, python follow.py, wg show, ...)
#   ./lab.sh restart alice       reboot alice's machine
#   ./lab.sh tap <tcpdump args>  watch the LAN from outside (optional); -w /state/x.pcap saves to state/x.pcap
#   ./lab.sh down                stop the machines; state survives
#   ./lab.sh clean               stop the machines and delete all state
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  up)      mkdir -p state; docker compose up --build -d --wait "${@:2}" ;;
  conductor) mkdir -p state; CONDUCTOR=did:web:conductor.stage docker compose --profile conductor up --build -d --wait
           echo "the conductor is up: open http://localhost:8080" ;;
  test)    "$0" clean >/dev/null 2>&1; SCORE="${2:-GETTING_STARTED}" "$0" conductor >/dev/null
           docker compose exec -T conductor python play.py --speed "${SPEED:-4}" ;;
  down)    docker compose --profile conductor down ;;
  clean)   docker compose --profile conductor down; docker run --rm -v "$PWD/state:/state" alpine:3.20 sh -c 'rm -rf /state/*' ;;
  logs)    docker compose logs -f --no-log-prefix "${2:?machine}" ;;
  restart) docker compose restart "${2:?machine}" ;;
  tap)     docker compose run --rm tap "${@:2}" ;;
  alice|bob|eve) if [ -t 0 ]; then docker compose exec "$@"; else docker compose exec -T "$@"; fi ;;
  *) sed -n '2,11p' "$0"; exit 1 ;;
esac
