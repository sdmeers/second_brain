#!/usr/bin/env bash
# Copy the project documentation into the Obsidian vault so it renders natively.
#
# Destination: $OBSIDIAN_VAULT_PATH/02_Projects/Personal/Second Brain/docs/
#
# Only the files listed below are touched. Vault-only material in that folder
# (Syncthing/, old/, and any notes you have written there) is left alone.
#
# Usage:  ./scripts/sync_docs_to_vault.sh [--dry-run]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read the vault path from backend/.env rather than hardcoding it
VAULT_PATH="$(grep -E '^OBSIDIAN_VAULT_PATH=' "$REPO_ROOT/backend/.env" 2>/dev/null \
  | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"

if [[ -z "${VAULT_PATH}" || ! -d "${VAULT_PATH}" ]]; then
  echo "ERROR: could not resolve OBSIDIAN_VAULT_PATH from backend/.env" >&2
  exit 1
fi

DEST="${VAULT_PATH}/02_Projects/Personal/Second Brain/docs"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# source-in-repo : name-in-vault
FILES=(
  "README.md:README.md"
  "docs/user_guide.md:user_guide.md"
  "docs/technical_implementation.md:technical_implementation.md"
  "docs/manual_testing.md:manual_testing.md"
  "docs/changelog.md:changelog.md"
  "docs/product_description.md:product_description.md"
)

if [[ $DRY_RUN -eq 0 ]]; then
  mkdir -p "$DEST"
fi

echo "Syncing docs -> ${DEST}"
for entry in "${FILES[@]}"; do
  src="${REPO_ROOT}/${entry%%:*}"
  dst="${DEST}/${entry##*:}"

  if [[ ! -f "$src" ]]; then
    echo "  SKIP    ${entry%%:*} (not found)"
    continue
  fi

  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    echo "  same    ${entry##*:}"
    continue
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  WOULD   ${entry##*:}"
  else
    cp "$src" "$dst"
    echo "  copied  ${entry##*:}"
  fi
done

echo "Done."
