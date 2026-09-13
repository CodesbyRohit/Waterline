#!/usr/bin/env python3
"""
Buy or Wait? - entry point.

The full deterministic decision engine lives in engine/main.py.
Run this file to regenerate output.csv from dataset/.

    python code/main.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.main import run_pipeline  # noqa: E402


if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run_pipeline(
        os.path.join(project_root, "dataset"),
        os.path.join(project_root, "output.csv"),
    )
