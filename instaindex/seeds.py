"""Seed-set loading.

The seed set is the single most important input to this system. It defines what
"good" means: the ranking answers "who is authoritative *relative to these
accounts*", so changing the seeds changes the topic. Everything else is
mechanism.

Practical guidance for building one:

  * 20-100 accounts is the useful range. Fewer and the ranking inherits one
    person's idiosyncratic taste; many more and you have already made the
    editorial judgement the algorithm was supposed to make for you.
  * Pick accounts you would defend individually. A single bad seed pollutes
    everything downstream of it.
  * Spread across sub-communities (ML research, math education, systems
    programming, physics, ...). A seed set drawn from one clique returns that
    clique.
  * Seeds should be *followed by* the community, not just prolific. The
    algorithm reads follows, not posts.
  * Re-run with several different seed sets. Names that survive all of them are
    the robust picks; names that appear under only one are that seed's friends.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

DEFAULT_SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seeds"
)


def load_seed_file(path: str) -> Tuple[List[str], Dict[str, float]]:
    """Load a seed file. Returns ``(keys, weights)``.

    Accepted shapes::

        ["alice", "bob"]
        {"seeds": ["alice", "bob"]}
        {"seeds": [{"handle": "alice", "weight": 2.0}, {"handle": "bob"}]}

    A plain-text file with one handle per line also works, so you can paste a
    list straight out of a notes app.
    """
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()

    if not raw:
        return [], {}

    keys: List[str] = []
    weights: Dict[str, float] = {}

    if raw[0] in "[{":
        data = json.loads(raw)
        entries = data.get("seeds", []) if isinstance(data, dict) else data
        for entry in entries:
            if isinstance(entry, str):
                key = entry
                weight = 1.0
            elif isinstance(entry, dict):
                key = entry.get("handle") or entry.get("id") or entry.get("username")
                weight = float(entry.get("weight", 1.0))
            else:
                continue
            if not key:
                continue
            key = key.strip().lstrip("@")
            keys.append(key)
            weights[key] = weight
    else:
        for line in raw.splitlines():
            line = line.split("#", 1)[0].strip().lstrip("@")
            if line:
                keys.append(line)
                weights[line] = 1.0

    return keys, weights


def resolve_seed_argument(value: str) -> Tuple[List[str], Dict[str, float]]:
    """Interpret ``--seeds``: a file path, a named preset, or a comma list."""
    if os.path.exists(value):
        return load_seed_file(value)

    preset = os.path.join(DEFAULT_SEED_DIR, f"{value}.json")
    if os.path.exists(preset):
        return load_seed_file(preset)

    keys = [k.strip().lstrip("@") for k in value.split(",") if k.strip()]
    return keys, {k: 1.0 for k in keys}


def list_presets() -> List[str]:
    if not os.path.isdir(DEFAULT_SEED_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(DEFAULT_SEED_DIR)
        if f.endswith(".json")
    )
