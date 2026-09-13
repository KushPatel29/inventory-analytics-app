"""
Local development entry point.

The generated sample is loaded by default here, because the first thing anyone
does with a clone is run it, and an empty dashboard with an upload box on it
does not show whether the clone works. Set ``DEMO_AUTOLOAD=0`` to start empty
and upload a workbook by hand; the hosted demo sets it explicitly either way.
"""

import os

os.environ.setdefault("DEMO_AUTOLOAD", "1")

from invapp import app  # noqa: E402  - must follow the environment default

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
