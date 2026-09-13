#!/usr/bin/env bash
# Copy the project documentation into the Obsidian vault so it renders natively.
#
# Destination: $OBSIDIAN_VAULT_PATH/03_Areas/Coding/Second Brain/docs/
# (The project folder under 02_Projects was archived on 2026-09-12; don't point this back there.)
#
# Only the files listed below are touched. Vault-only material in that folder
# (Syncthing/, old/, and any notes you have written there) is left alone.
#
# Usage:  ./scripts/sync_docs_to_vault.sh [--dry-run]

set -euo pipefail

# RETIRED 2026-09-13. The Second Brain project was archived on 2026-09-12 and the vault
# docs are now hand-maintained in 03_Areas/Coding/Second Brain/ (different filenames, no
# docs/ subfolder), so copying here would either recreate the archived project folder or
# duplicate those notes. Kept only until it is deleted.
echo "sync_docs_to_vault.sh is retired: vault docs live in 03_Areas/Coding/Second Brain/ and are maintained by hand." >&2
exit 1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Read the vault path from backend/.env rather than hardcoding it
VAULT_PATH="$(grep -E '^OBSIDIAN_VAULT_PATH=' "$REPO_ROOT/backend/.env" 2>/dev/null \
  | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"

if [[ -z "${VAULT_PATH}" || ! -d "${VAULT_PATH}" ]]; then
  echo "ERROR: could not resolve OBSIDIAN_VAULT_PATH from backend/.env" >&2
  exit 1
fi

DEST="${VAULT_PATH}/03_Areas/Coding/Second Brain/docs"

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
