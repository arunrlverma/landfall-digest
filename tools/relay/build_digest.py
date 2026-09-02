#!/usr/bin/env python3
"""Build the daily spiritual-podcast digest, once, for everybody.

This is the relay. It does exactly what the app's on-device `SpiritualDigest`
does, but on a schedule instead of on a phone:

    ~1,300 charting shows -> this week's episodes -> keyword prefilter
    -> LLM scoring -> digest.json

The app fetches the published file and renders it instantly. It keeps the
on-device path as a fallback, so an unreachable relay degrades to slow rather
than broken.

There is no server. It runs in GitHub Actions on a cron and commits the result;
the "backend" is a static file in a git repo.

Why it exists: the on-device scan fetches hundreds of RSS feeds from a phone,
which is slow and burns battery, and every user repeats identical work. The
digest is not personalized, so computing it once is strictly better.

Usage:
    OPENAI_API_KEY=... python3 tools/relay/build_digest.py --out digest.json
    ... --dry-run     # skip the paid scoring step, print the shortlist
    ... --edition stoicism   # editions/stoicism.json: extra terms, tradition
                             # constraint, public/editions/stoicism/digest.json

An edition file is {"traditions": [...], "strong": [...], "weak": [...],
"veto": [...], "promptNote": "..."}. Its terms are added to the shared lists
(matched as whole words), its traditions constrain the model's label set and
are enforced as a second gate on the picks, and its note is appended to the
system prompt. The shared digest (no --edition) is unchanged.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Landfall/1.0 (+digest relay)"}
CHART = "https://itunes.apple.com/us/rss/toppodcasts/limit={limit}/{genre}json"
LOOKUP = "https://itunes.apple.com/lookup"
CHAT = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4.1-mini"

# Every category, because the whole point is the 98.5% of charting shows that
# are NOT tagged Religion & Spirituality — measured: only 19 of ~1,294 are.
GENRES = [None, 1489, 1324, 1533, 1512, 1303, 1321, 1304, 1301, 1487, 1488,
          1314, 1545, 1318, 1309, 1310, 1305]

# Mirrors Sources/SpiritualFilter.swift. Keep the two in step: the app still
# runs this locally when the relay is unreachable.
STRONG = r"""enlighten|meditat|mindfulness|buddhis|dharma|nondual|contemplat|mystic|
theolog|monastic|monk|nun|sufi|vedanta|kabbalah|scripture|gospel|torah|quran|
bhagavad|gita|upanishad|sutra|psalm|parable|liturgy|reincarnat|transcenden|
near-death|afterlife|salvation|consciousness|psychedelic|ayahuasca|psilocybin|
spiritual|prayer|praying|sabbath|pilgrim|seminary|rabbi|imam|priest|chaplain|
zen|taoism|stoicism|stoic"""
WEAK = r"""god|faith|soul|sacred|divine|meaning of life|purpose|mortality|grief|
forgiveness|surrender|ego|silence|solitude|ritual|belief|suffering|compassion|
gratitude|wisdom|awe|eternity|devotion|humility|redemption"""
VETO = r"""murder|killer|killing|homicide|suspect|detective|verdict|convicted|
cold case|serial killer|true crime|missing person|disappear|kidnap|trafficking|
autopsy|nba|nfl|mlb|playoff|quarterback|box score|fantasy football|draft pick|
power rankings|betting odds|recap|highlights|trade deadline"""


def _re(terms: str) -> re.Pattern:
    """Word-boundary matcher.

    The leading \\b is load-bearing and was learned the hard way: a plain
    substring check made "nfl" match inside "i(nfl)uence", so an interview with
    an exorcist priest about diabolical *influence* was vetoed as sports talk.
    """
    joined = "|".join(t.strip() for t in terms.replace("\n", "").split("|") if t.strip())
    return re.compile(rf"\b(?:{joined})", re.I)


STRONG_RE, WEAK_RE, VETO_RE = _re(STRONG), _re(WEAK), _re(VETO)

# Set by --edition: the constrained tradition list and the note for the prompt.
EDITION: dict | None = None


def _word_re(terms: list[str]) -> str:
    """Edition terms as whole words/phrases: "mass", "ward" or "alma" must not
    fire inside "massive", "warden" or "almanac"."""
    return "|".join(rf"{re.escape(t.strip())}\b" for t in terms if t.strip())


def load_edition(key: str, root: str = "editions") -> dict:
    """Read editions/<key>.json and compose the edition's matchers.

    The shared STRONG/WEAK/VETO lists stay in force; the edition adds to them.
    Returns the parsed file (traditions, promptNote) for the prompt."""
    global STRONG_RE, WEAK_RE, VETO_RE, EDITION
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", key):
        raise SystemExit(f"invalid edition key: {key}")
    path = os.path.join(root, f"{key}.json")
    with open(path, encoding="utf-8") as f:
        edition = json.load(f)
    traditions = edition.get("traditions")
    if not isinstance(traditions, list) or not traditions:
        raise SystemExit(f"{path}: traditions must be a non-empty list")
    unknown = sorted(set(traditions) - set(TRADITIONS))
    if unknown:
        raise SystemExit(f"{path}: unknown traditions {unknown}; allowed: {TRADITIONS}")
    for name in ("strong", "weak", "veto"):
        value = edition.get(name, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise SystemExit(f"{path}: {name} must be a list of strings")
    strong = STRONG + ("|" + _word_re(edition["strong"]) if edition.get("strong") else "")
    weak = WEAK + ("|" + _word_re(edition["weak"]) if edition.get("weak") else "")
    veto = VETO + ("|" + _word_re(edition["veto"]) if edition.get("veto") else "")
    STRONG_RE, WEAK_RE, VETO_RE = _re(strong), _re(weak), _re(veto)
    EDITION = {"key": key, "traditions": list(traditions),
               "promptNote": str(edition.get("promptNote") or "").strip()}
    return EDITION


def system_prompt() -> str:
    """The scoring prompt, with the edition's tradition constraint and note."""
    # str.replace, not str.format: the prompt's JSON example has braces.
    if EDITION is None:
        return SYSTEM_PROMPT.replace("{traditions}", ", ".join(TRADITIONS))
    allowed = ", ".join(EDITION["traditions"])
    text = SYSTEM_PROMPT.replace("{traditions}", allowed)
    text += (
        f"\nThis listener uses the {EDITION['key']} edition. Only the traditions listed "
        f"above are wanted: an episode that belongs to another tradition is a 0-3 "
        f"regardless of quality.\n")
    if EDITION["promptNote"]:
        text += EDITION["promptNote"] + "\n"
    return text

TRADITIONS = ["Christian", "Catholic", "Jewish", "Muslim", "Buddhist", "Hindu", "Taoist",
              "Stoic", "Latter-day Saint", "Mythic", "Indigenous", "Interfaith", "Secular"]

SYSTEM_PROMPT = """\
You help a listener find podcast episodes worth their attention. They are \
reading the Bhagavad Gita, the Gospels, the Upanishads and the Tao Te Ching, \
and they want conversations that sit with the same questions: meaning, \
mortality, faith and doubt, consciousness, ethics, suffering, how to live.

They are open to every tradition and to none — a Jesuit, a Zen teacher, a \
neuroscientist on meditation, and a philosopher on death all qualify. What \
matters is that the episode genuinely dwells on the question, not that it uses \
religious vocabulary.

You receive a JSON object mapping index strings to episodes. Return a JSON \
object with EXACTLY the same keys. Each value is an object:
  {"rating": 0-10, "reason": "...", "tradition": "..."}

rating    how much this rewards a contemplative listener.
          9-10 a substantial conversation on an ultimate question
          7-8  a serious treatment of meaning, ethics, or inner life
          4-6  touches these themes but is mostly about something else
          0-3  uses the words incidentally, or is self-help, business
               motivation, true crime, sports, or politics wearing the vocabulary
reason    ONE sentence, under 18 words, addressed to the listener, saying what \
          they will actually get. No hype, no "dive into", no "explores the \
          fascinating world of". Concrete and plain.
tradition one of: {traditions}.

Be strict. A motivational episode about "purpose" at work is a 2. An episode \
where someone describes losing their faith is an 8. When uncertain, rate lower.
"""


def get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chart_pool(limit: int) -> list[dict]:
    """Every charting show across every genre, de-duplicated, with feed URLs."""
    ids, seen = [], set()
    for genre in GENRES:
        url = CHART.format(limit=limit, genre=f"genre={genre}/" if genre else "")
        try:
            feed = get_json(url)["feed"]
        except Exception as exc:                       # one bad chart is survivable
            print(f"  chart {genre}: {exc}", file=sys.stderr)
            continue
        entries = feed.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            pid = e["id"]["attributes"]["im:id"]
            if pid not in seen:
                seen.add(pid)
                ids.append(pid)
        time.sleep(0.15)

    shows = []
    for i in range(0, len(ids), 20):
        params = urllib.parse.urlencode(
            {"id": ",".join(ids[i:i + 20]), "entity": "podcast"})
        try:
            results = get_json(f"{LOOKUP}?{params}")["results"]
        except Exception:
            continue
        for r in results:
            if r.get("feedUrl"):
                shows.append({
                    "title": r.get("collectionName", ""),
                    "feed": r["feedUrl"],
                    "genre": r.get("primaryGenreName", ""),
                    "artwork": r.get("artworkUrl600") or r.get("artworkUrl100"),
                })
        time.sleep(0.1)
    return shows


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
    # Regex parsing leaves XML entities intact — "&amp;" was showing up
    # verbatim in episode titles. Unescape after tag-stripping.
    return html.unescape(strip_tags(m.group(1))) if m else ""


def recent_episodes(show: dict, since: datetime) -> list[dict]:
    try:
        req = urllib.request.Request(show["feed"], headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return []

    tagged = show["genre"] == "Religion & Spirituality"
    out = []
    for m in list(re.finditer(r"<item>(.*?)</item>", raw, re.S))[:8]:
        block = m.group(1)
        title = field(block, "title")
        summary = field(block, "description")[:600]
        enclosure = re.search(r'<enclosure[^>]*url="([^"]+)"', block)
        if not title or not enclosure:
            continue

        # The freshness window. `since` had been threaded through unused, so a
        # slow feed's evergreen episode could chart in the digest for months.
        pub = field(block, "pubDate")
        if pub:
            try:
                when = parsedate_to_datetime(pub)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when < since:
                    continue
            except (TypeError, ValueError):
                pass  # unparseable dates keep the episode; windowing is best-effort

        haystack = f"{title} {summary}".lower()
        if not tagged and VETO_RE.search(haystack):
            continue
        strong = set(x.group(0).lower() for x in STRONG_RE.finditer(haystack))
        weak = set(x.group(0).lower() for x in WEAK_RE.finditer(haystack))
        if not (strong or len(weak) >= (1 if tagged else 3)):
            continue

        out.append({
            "id": field(block, "guid") or enclosure.group(1),
            "showID": show["feed"],
            "showTitle": show["title"],
            "title": title,
            "summary": summary,
            "audioURL": enclosure.group(1),
            "published": field(block, "pubDate"),
            "artworkURL": show["artwork"],
            "_score": len(strong) * 3 + len(weak) + (2 if tagged else 0),
        })
    return out


def score(candidates: list[dict], key: str, batch: int = 15) -> list[dict]:
    """The one paid step. A failed batch is skipped, never fatal."""
    picks = []
    for i in range(0, len(candidates), batch):
        chunk = candidates[i:i + batch]
        payload = {
            str(n): {"show": c["showTitle"], "title": c["title"],
                     "about": c["summary"][:500]}
            for n, c in enumerate(chunk)
        }
        body = json.dumps({
            "model": MODEL,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": json.dumps(payload)},
            ],
        }).encode()
        req = urllib.request.Request(
            CHAT, data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", **UA})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                content = json.load(r)["choices"][0]["message"]["content"]
            verdicts = json.loads(content)
        except Exception as exc:
            print(f"  scoring batch {i}: {exc}", file=sys.stderr)
            continue
        verdicts = verdicts.get("episodes", verdicts.get("results", verdicts))
        for n, c in enumerate(chunk):
            v = verdicts.get(str(n))
            if not isinstance(v, dict):
                continue
            try:
                rating = int(v["rating"])
            except (KeyError, TypeError, ValueError):
                continue
            reason = str(v.get("reason", "")).strip()
            if not reason or not 0 <= rating <= 10:
                continue
            picks.append({**{k: c[k] for k in c if not k.startswith("_")},
                          "rating": rating,
                          "reason": reason,
                          "tradition": str(v.get("tradition", "Secular"))})
    return picks


def load_history(path: str) -> dict[str, str]:
    """The rotation ledger from the previous digest: episode id -> the date it
    was last featured. Digests that predate the ledger seed it from their own
    picks, so rotation starts working on the very next daily build."""
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return {}
    history = {k: v for k, v in (prev.get("history") or {}).items()
               if isinstance(k, str) and isinstance(v, str)}
    day = (prev.get("generatedAt") or "")[:10]
    if day:
        for p in prev.get("picks", []):
            if p.get("id"):
                history.setdefault(p["id"], day)
    return history


def rotate(picks: list[dict], history: dict[str, str], keep: int,
           cooldown_days: int = 3, floor: int = 8) -> list[dict]:
    """Yesterday's page should not be today's page. Bench anything featured on
    a previous day within the cooldown; hourly re-runs the SAME day are not
    benched, so today's list holds still all day (the app treats the digest as
    daily). If benching leaves the page thin, the longest-rested benched picks
    return — rotation must never publish a sparse digest."""
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    cooloff = (now - timedelta(days=cooldown_days)).date().isoformat()

    def resting(p: dict) -> bool:
        day = history.get(p["id"], "")
        return bool(day) and day != today and day >= cooloff

    fresh = [p for p in picks if not resting(p)]
    benched = [p for p in picks if resting(p)]
    if len(fresh) < floor:
        benched.sort(key=lambda p: history.get(p["id"], ""))
        fresh += benched[:floor - len(fresh)]
    print(f"  rotation: {len(fresh[:keep])} kept, {len(benched)} resting")
    return fresh[:keep]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="digest.json (public/editions/<key>/digest.json with --edition)")
    ap.add_argument("--edition", default=None, help="edition key: reads editions/<key>.json")
    ap.add_argument("--limit", type=int, default=100, help="shows per genre chart")
    ap.add_argument("--shows", type=int, default=400, help="max feeds to fetch")
    ap.add_argument("--keep", type=int, default=25)
    ap.add_argument("--min-picks", type=int, default=3,
                    help="refuse to write an edition digest with fewer picks")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.edition:
        edition = load_edition(args.edition)
        print(f"edition {edition['key']}: traditions {edition['traditions']}")
        if args.out is None:
            args.out = os.path.join("public", "editions", args.edition, "digest.json")
    elif args.out is None:
        args.out = "digest.json"

    print("charts…")
    shows = chart_pool(args.limit)
    print(f"  {len(shows)} shows with feeds")

    # Most promising first, so a capped run still sees the best candidates.
    shows.sort(key=lambda s: s["genre"] != "Religion & Spirituality")
    shows = shows[:args.shows]

    since = datetime.now(timezone.utc) - timedelta(days=8)
    print(f"feeds… ({len(shows)})")
    candidates = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for got in ex.map(lambda s: recent_episodes(s, since), shows):
            candidates += got
    candidates.sort(key=lambda c: -c["_score"])
    # At most two per show. A daily sermon feed can otherwise fill the whole
    # digest with itself — six Joel Osteen episodes crowded out everything else
    # in testing — and the point is breadth across the whole chart.
    per_show: dict[str, int] = {}
    varied = []
    for c in candidates:
        n = per_show.get(c["showID"], 0)
        if n >= 2:
            continue
        per_show[c["showID"]] = n + 1
        varied.append(c)
    candidates = varied[:80]
    print(f"  {len(candidates)} candidates after prefilter")

    if args.dry_run:
        for c in candidates[:25]:
            print(f"  {c['_score']:>3}  {c['showTitle'][:24]:26} | {c['title'][:58]}")
        return 0

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    print("scoring…")
    picks = score(candidates, key)
    picks = [p for p in picks if p["rating"] >= 6]
    if EDITION is not None:
        # Second gate: the prompt constrains the label set, but a model that
        # strays outside it must not leak another tradition into the edition.
        allowed = set(EDITION["traditions"])
        before = len(picks)
        picks = [p for p in picks if p["tradition"] in allowed]
        print(f"  tradition gate: {len(picks)} of {before} picks in {sorted(allowed)}")
    picks.sort(key=lambda p: -p["rating"])
    # Rotate over the FULL scored list, then cap — the whole point is that the
    # next-best fresh episodes take the seats of the ones that are resting.
    history = load_history(args.out)
    picks = rotate(picks, history, keep=args.keep)
    print(f"  {len(picks)} kept")
    if EDITION is not None and len(picks) < args.min_picks:
        # Never publish a thin edition over a good one — the previous file
        # stays and the workflow's sanity step would refuse it anyway.
        print(f"only {len(picks)} picks for edition {EDITION['key']}; "
              f"refusing to write {args.out}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    for p in picks:
        history[p["id"]] = today
    cutoff = (now - timedelta(days=14)).date().isoformat()
    history = {k: v for k, v in history.items() if v >= cutoff}

    payload = {
        "version": 1,
        **({"edition": EDITION["key"], "traditions": EDITION["traditions"]} if EDITION else {}),
        "generatedAt": now.isoformat(),
        "showsScanned": len(shows),
        "candidates": len(candidates),
        "picks": picks,
        # The rotation ledger rides inside the digest itself — the relay's only
        # storage is this file. The app's decoder ignores unknown keys.
        "history": history,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
