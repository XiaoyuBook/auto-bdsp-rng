"""Compatibility import for the standalone test tool."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from auto_bdsp_rng.notifications.qq_client import QQClient, QQError
