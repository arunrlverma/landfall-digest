# landfall-digest

The discovery relay for [Landfall]. Every hour, a GitHub Action scans the top
~1,300 charting podcasts across every Apple category, keyword-prefilters this
week's episodes, has an LLM score the shortlist for contemplative relevance,
and commits the result to `public/digest.json`. The app fetches that one file.

No server. The failure mode is "the app builds its own digest locally" — never
a blank screen.

## Editions

Each Landfall edition gets its own digest, once a day, from `editions/<key>.json`
(`traditions`, extra `strong` / `weak` / `veto` terms matched as whole words, and a
`promptNote` for the scorer). The workflow's daily matrix runs

    python3 tools/relay/build_digest.py --edition <key>

and commits `public/editions/<key>/digest.json`; the shared hourly digest is
unchanged. The tradition list constrains the model's labels and is enforced again
on the picks; an edition with fewer than three picks is not written, so a thin day
keeps yesterday's file. `tools/relay/check_digest.py` is the publish gate for both.
The term files are mirrored from the app repo's `Editions/<key>/digest-terms.json`.
