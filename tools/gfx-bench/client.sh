#!/bin/sh
exec stdbuf -oL -eL "$@" > /tmp/wlt/client.log 2>&1
