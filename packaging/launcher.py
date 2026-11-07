"""Frozen-application entry point (PyInstaller)."""
import sys

from aircraft_foundry.app.main import main

sys.exit(main())
