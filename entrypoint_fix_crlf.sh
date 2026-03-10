#!/bin/bash
# Fix CRLF line endings in shell scripts from Windows bind mounts
for f in /code/*.sh; do
  tr -d '\r' < "$f" > "$f.tmp" && mv "$f.tmp" "$f"
done
exec bash startup.sh
