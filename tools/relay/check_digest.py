#!/usr/bin/env python3
"""Refuse to publish an empty or broken digest over a good one.

    check_digest.py public/digest.json
    check_digest.py public/editions/stoicism/digest.json --edition stoicism

A bad scrape would otherwise blank the app's Today tab for a whole day. An
edition digest must also carry its key and only picks from its traditions.
"""
import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--edition", default=None)
    ap.add_argument("--min-picks", type=int, default=3)
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        d = json.load(f)
    picks = d.get("picks", [])
    if len(picks) < args.min_picks:
        sys.exit(f"only {len(picks)} picks; refusing to publish")
    for p in picks:
        assert p.get("audioURL") and p.get("title") and p.get("reason"), p.get("id")
    if args.edition:
        if d.get("edition") != args.edition:
            sys.exit(f"digest edition is {d.get('edition')!r}, expected {args.edition!r}")
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "..", "editions", f"{args.edition}.json"), encoding="utf-8") as f:
            allowed = set(json.load(f)["traditions"])
        stray = sorted({p.get("tradition") for p in picks} - allowed)
        if stray:
            sys.exit(f"picks outside the edition's traditions: {stray}")
    print(f"ok: {len(picks)} picks" + (f" for {args.edition}" if args.edition else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
