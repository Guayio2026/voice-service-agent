#!/usr/bin/env bash
# Generate an nginx basic-auth password file.
# Usage (on the server): sudo bash deploy/make-htpasswd.sh member admin
# Prompts for each password; openssl is preinstalled on Ubuntu.
set -euo pipefail

OUT="${HTPASSWD_PATH:-/etc/nginx/.htpasswd}"

if [ $# -eq 0 ]; then
  echo "usage: $0 username [username ...]" >&2
  exit 1
fi

sudo touch "$OUT"
sudo chmod 640 "$OUT"
for user in "$@"; do
  read -rsp "Password for ${user}: " pw; echo
  hash=$(openssl passwd -apr1 "$pw")
  printf '%s:%s\n' "$user" "$hash" | sudo tee -a "$OUT" > /dev/null
done
echo "written: $OUT"
