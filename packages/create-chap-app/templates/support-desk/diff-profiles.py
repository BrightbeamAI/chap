"""The support desk workload under two profile sets, side by side.

    python diff-profiles.py --against core/1.0
    python diff-profiles.py --json --against core/1.0,review/1.0,modes/1.0

The decisions here are scripted, because this is a comparison and no person
is at the desk. In the project itself the decision is always made in the desk.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

from profiles_diff import main  # noqa: E402

if __name__ == "__main__":
    config = json.loads((HERE / "chap.config.json").read_text(encoding="utf-8"))
    main("support-desk", config["profiles"])
