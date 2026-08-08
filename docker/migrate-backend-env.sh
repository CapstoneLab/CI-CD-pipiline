#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_container="${1:-capstone-back}"
output_file="${2:-$repo_root/.env.backend}"

if [[ -e "$output_file" ]]; then
  echo "Refusing to overwrite existing backend env file: $output_file" >&2
  exit 1
fi

if ! docker inspect "$source_container" >/dev/null 2>&1; then
  echo "Backend container not found: $source_container" >&2
  exit 1
fi

temp_dir="$(mktemp -d)"
trap 'rm -rf -- "$temp_dir"' EXIT
raw_env="$temp_dir/container.env"
filtered_env="$temp_dir/backend.env"

umask 077
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
  "$source_container" > "$raw_env"

while IFS= read -r line; do
  key="${line%%=*}"
  case "$key" in
    DATABASE_URL|JWT_SECRET|ENGINE_SHARED_TOKEN|GITHUB_CLIENT_ID|GITHUB_CLIENT_SECRET|GITHUB_REDIRECT_URI|FRONTEND_REDIRECT_URL|ALLOWED_ORIGINS|WINDOWS_CALLBACK_BASE_URL|UBUNTU_SSH_HOST|UBUNTU_SSH_PORT|UBUNTU_SSH_USER|UBUNTU_SSH_PASSWORD|UBUNTU_PYTHON_COMMAND|UBUNTU_RUNNER_PATH|UBUNTU_WORKING_DIR)
      printf '%s\n' "$line" >> "$filtered_env"
      ;;
  esac
done < "$raw_env"

for required_key in DATABASE_URL JWT_SECRET ENGINE_SHARED_TOKEN; do
  if ! grep -q "^${required_key}=." "$filtered_env"; then
    echo "Existing container is missing required value: $required_key" >&2
    exit 1
  fi
done

install -m 600 "$filtered_env" "$output_file"
echo "Created $output_file with mode 600; secret values were not printed."
