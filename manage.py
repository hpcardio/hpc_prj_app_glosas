#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    root_dir = Path(__file__).resolve().parent
    frontend_dir = root_dir / "frontend"
    sys.path.insert(0, str(frontend_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "glosas_frontend.settings")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
