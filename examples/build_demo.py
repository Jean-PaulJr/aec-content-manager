"""Build the demo project:  python examples/build_demo.py [target_dir]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calcbook.demo import build  # noqa: E402

if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project"))
