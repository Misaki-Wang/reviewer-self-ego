# Source from a shell to use the existing MBZ runtime. No packages are installed.
REVIEWER_UTIL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$REVIEWER_UTIL_DIR/.runtime/ghostscript-9.55.0/bin:$PATH"
export PYTHONPATH="$REVIEWER_UTIL_DIR/.runtime/python_vendor:/usr/local/lib/python3.10/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="$REVIEWER_UTIL_DIR/.runtime/tmp"
mkdir -p -- "$TMPDIR"
unset REVIEWER_UTIL_DIR
