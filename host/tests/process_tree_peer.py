"""Synthetic descendant tree; never touches user workspaces."""

import os
from pathlib import Path
import subprocess
import sys
import time

mode, marker = sys.argv[1:3]
if mode == "leaf":
    staging = Path(marker).with_name(Path(marker).name + ".tmp")
    staging.write_text(str(os.getpid()), encoding="ascii")
    os.replace(staging, marker)
    time.sleep(60)
elif mode in ("tree", "exit"):
    child = subprocess.Popen([sys.executable, __file__, "middle", marker])
    if mode == "exit":
        deadline = time.monotonic() + 10
        while not Path(marker).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os._exit(0)
    child.wait()
elif mode == "middle":
    subprocess.Popen([sys.executable, __file__, "leaf", marker]).wait()
elif mode == "owner":
    from runvori.codex.stdio import StdioClient
    client = StdioClient([sys.executable, __file__, "tree", marker],
                         cwd=Path.cwd(), env=dict(os.environ))
    time.sleep(60)
