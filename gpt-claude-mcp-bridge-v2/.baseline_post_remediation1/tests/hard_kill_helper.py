"""Helper for the hard-termination test: behaves like a bridge that owns a child tree and is
then killed without any chance to run cleanup (TerminateProcess)."""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.jobobject import ProcessOwnership  # noqa: E402

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
owner = ProcessOwnership()
if not owner.attach(child):
    child.kill()
    sys.exit(3)
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(600)
