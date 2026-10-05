#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'No se encontró el entorno Python del proyecto (.venv/bin/python).'
  exit 1
fi
if curl --silent --fail 'http://127.0.0.1:5052/teams/roadmap' >/dev/null; then
  open 'http://127.0.0.1:5052/dev/login?next=/teams'
  exit 0
fi
.venv/bin/python tools/run_flow_teams_local.py &
flow_teams_pid=$!
trap 'kill "$flow_teams_pid" 2>/dev/null || true' EXIT INT TERM
for attempt in {1..30}; do
  if curl --silent --fail 'http://127.0.0.1:5052/login' >/dev/null; then
    open 'http://127.0.0.1:5052/dev/login?next=/teams'
    wait "$flow_teams_pid"
    exit 0
  fi
  sleep 1
done
print 'No se pudo abrir Flow Teams en el puerto 5052. Revisa el mensaje anterior.'
exit 1
