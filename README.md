# landfall-digest

The discovery relay for [Landfall]. Every hour, a GitHub Action scans the top
~1,300 charting podcasts across every Apple category, keyword-prefilters this
week's episodes, has an LLM score the shortlist for contemplative relevance,
and commits the result to `public/digest.json`. The app fetches that one file.

No server. The failure mode is "the app builds its own digest locally" — never
a blank screen.
