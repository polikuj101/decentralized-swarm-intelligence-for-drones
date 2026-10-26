#!/usr/bin/env bash
for k in 1 2 3 4; do
  ps -eo rss,etimes,comm --sort=-rss | grep -E "ruby|gz" | grep -v grep | head -2 | while read rss et comm; do echo "$(date +%T) $comm $((rss/1024)) MB (process age ${et}s)"; done
  echo "  free: $(free -m | awk 'NR==2{print $3" used / "$7" available MB"}')"
  sleep 60
done
