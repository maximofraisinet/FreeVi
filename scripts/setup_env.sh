#!/usr/bin/env bash
# Setup .env from .env.example
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLE="$HERE/.env.example"
DEST="$HERE/.env"

if [ ! -f "$EXAMPLE" ]; then
  echo "Cannot find .env.example in project root." >&2
  exit 1
fi

if [ -f "$DEST" ]; then
  echo ".env already exists. Leaving it untouched." >&2
  exit 0
fi

cp "$EXAMPLE" "$DEST"
chmod 600 "$DEST"

cat <<EOF
.env has been created from .env.example at: $DEST
Edit it and fill in PEXELS_API_KEY and any other values.

To load it in your current shell (bash/zsh):
  export 
  set -a; source "$DEST"; set +a

Or use the GUI/CLI which will automatically load .env via python-dotenv.
EOF

exit 0
