"""Internal Windows gate: never launch the target before job assignment."""

import os
import subprocess
import sys

if __name__ == "__main__":
    # Raw single-byte read avoids prefetching subsequent protocol bytes.
    if os.read(0, 1) != b"\x00":
        sys.exit(125)
    try:
        child = subprocess.Popen(sys.argv[1:])
        sys.exit(child.wait())
    except OSError:
        sys.exit(126)
