#!/usr/bin/env bash
# Chromium needs a display and something to play into; neither exists in a container.
set -euo pipefail

export DISPLAY=:99
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
rm -f /tmp/.X99-lock

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
pulseaudio --start --exit-idle-time=-1 --disallow-exit --log-target=stderr

# A silent default mic: Meet then has nothing real to send, whatever its buttons say.
pactl load-module module-null-sink sink_name=silence >/dev/null
pactl set-default-source silence.monitor

# `login` is the only command a person has to see. Compose binds this to localhost.
if [ "${1:-}" = "login" ]; then
  x11vnc -display :99 -forever -shared -nopw -localhost -quiet -bg
  websockify --web=/usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
  echo "Open http://localhost:6080/vnc.html to sign in."
fi

# For tests: run anything inside the display and audio set up above.
if [ "${1:-}" = "exec" ]; then
  shift
  exec "$@"
fi

exec localscribe-bot "$@"
