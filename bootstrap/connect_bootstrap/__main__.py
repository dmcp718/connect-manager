"""Entry point for `connect-bootstrap`."""

from __future__ import annotations

import sys

from connect_bootstrap.app import BootstrapApp


def main() -> int:
    app = BootstrapApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
