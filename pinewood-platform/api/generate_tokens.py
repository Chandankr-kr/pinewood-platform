"""Generate the three test tokens (one per role) for exercising the API.

    python -m api.generate_tokens

Writes api/test_tokens.json and prints ready-to-paste curl examples.
"""
from __future__ import annotations

import json
from pathlib import Path

from .auth import create_token

TEST_PRINCIPALS = [
    {
        "label": "Corporate Admin (sees everything)",
        "sub": "admin@pinewood.example",
        "role": "corporate_admin",
        "region": None,
        "community_id": None,
    },
    {
        "label": "Regional Director — Pacific Northwest (OR communities C001-C005)",
        "sub": "rd.pnw@pinewood.example",
        "role": "regional_director",
        "region": "Pacific Northwest",
        "community_id": None,
    },
    {
        "label": "Community Executive Director — C011 (Pinewood Austin)",
        "sub": "ed.c011@pinewood.example",
        "role": "community_ed",
        "region": None,
        "community_id": "C011",
    },
]


def main():
    out = []
    for p in TEST_PRINCIPALS:
        token = create_token(
            sub=p["sub"], role=p["role"],
            region=p["region"], community_id=p["community_id"],
        )
        out.append({**p, "token": token})

    path = Path(__file__).resolve().parent / "test_tokens.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"Wrote {path}\n")
    for p in out:
        print(f"## {p['label']}")
        print(f"Role: {p['role']}")
        print(f"Token:\n{p['token']}\n")
        print("Example:")
        print(f'  curl -H "Authorization: Bearer {p["token"][:25]}..." '
              f'http://127.0.0.1:8000/occupancy\n')


if __name__ == "__main__":
    main()
