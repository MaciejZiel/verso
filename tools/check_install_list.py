"""Fails when a Python module in verso/ is missing from install_data() in verso/meson.build."""
import re
import sys
from pathlib import Path

package = Path(sys.argv[1])
listed = set(re.findall(r"'([\w]+\.py)'", (package / "meson.build").read_text()))
present = {path.name for path in package.glob("*.py")}
missing = sorted(present - listed)
if missing:
    print("Not installed by meson:", ", ".join(missing))
    sys.exit(1)
