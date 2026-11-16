#!/bin/bash
# Rebuild notes/ from the Obsidian vault.  Usage:  ./build-notes.sh
# Needs pandoc (brew install pandoc).
VAULT="${VAULT:-$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Dennis}"
cd "$(dirname "$0")" && python3 tools/build_notes.py "$VAULT"
