#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    # Adjust the baked-in subtitlarr user/group to match whatever uid/gid
    # the host actually wants (e.g. Unraid's nobody:users, 99:100) — a
    # bind-mounted /data's ownership comes from the HOST, not the image's
    # own build-time chown, so a fixed uid baked into the image is often
    # wrong on first run. Mirrors the PUID/PGID pattern LinuxServer.io
    # images use, which most Unraid users already expect.
    groupmod -o -g "$PGID" subtitlarr
    usermod -o -u "$PUID" subtitlarr
    chown -R subtitlarr:subtitlarr /data
    exec gosu subtitlarr "$@"
fi

exec "$@"
