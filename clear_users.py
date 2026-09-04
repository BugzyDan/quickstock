#!/usr/bin/env python
"""Compatibility wrapper for the guarded Django user purge command."""

import os
import sys


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_path = os.path.join(base_dir, "Website", "BackEnd", "quickstock")
    if project_path not in sys.path:
        sys.path.insert(0, project_path)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quickstock.settings")

    import django
    from django.core.management import call_command

    django.setup()
    call_command("purge_users", create_admin=True)


if __name__ == "__main__":
    main()
