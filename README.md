# landfall-digest

The discovery relay for [Landfall]. Every hour, a GitHub Action scans the top
~1,300 charting podcasts across every Apple category, keyword-prefilters this
week's episodes, has an LLM score the shortlist for contemplative relevance,
and commits the result to `public/digest.json`. The app fetches that one file.

No server. The failure mode is "the app builds its own digest locally" — never
a blank screen.

## Editions

Each Landfall edition gets its own digest, once a day, from `editions/<key>.json`
(`traditions`, the edition's own `charts`, its `strong` / `weak` / `veto` terms
matched as whole words, and a `promptNote` for the scorer). The workflow's daily
matrix runs

    python3 tools/relay/build_digest.py --edition <key>

and commits `public/editions/<key>/digest.json`; the shared hourly digest is
unchanged. An edition with fewer than three picks is not written, so a thin day
keeps yesterday's file. `tools/relay/check_digest.py` is the publish gate for both.
The term files are mirrored from the app repo's `Editions/<key>/digest-terms.json`.

A shelf carries what it is about, and every tradition has a shelf. Three rules keep
one shelf from filling with another's content — as the Stoicism shelf did on
2026-09-02, when it published Catholic apologetics and a Come, Follow Me study:

- **the edition's terms admit**, not the shared spiritual list. The shared lists are
  identical for all ten editions, so on their own they hand every shelf the same
  religious shortlist; they still veto and still rank, but an episode now needs one
  of the edition's own `strong` terms or three of its `weak` ones;
- **the scorer sees every tradition label**, never just the shelf's own. Narrowing
  the label set left an off-shelf episode nothing honest to say and it took the
  nearest catch-all ("Interfaith"), which then passed the tradition gate. It labels
  the episode where it belongs; the gate keeps what fits this shelf;
- **each edition brings its own charts**. Of 1,590 shows across the shared 17
  charts, one was Jewish and none were Muslim. `charts: [1441, 1532]` adds Apple's
  Judaism and Religion charts, and the Judaism shelf has a pool for the first time.
