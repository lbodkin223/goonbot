import re
import sys
import threading
import time
from urllib.parse import quote

import numpy as np

from . import config
from .db import DB, text_hash
from .embed import Embedder
from .fetch import Fetcher
from .parse import SITEMAP_INDEX, collection_from_url, parse_listing, parse_sitemap

STOP = set("a an the in on at of with and or for to is are be by from into my me i you his he him this that some who".split())
NUMBERS = set("one two three four five six seven eight nine ten first second third".split())


def tokens(s):
    s = re.sub(r"'s\b", "", s.lower())  # "cody's" -> "cody", not "cody", "s"
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in STOP and (len(t) > 1 or t.isdigit())]


def stem(t):
    return (t[:-1] if len(t) > 3 and t.endswith("s") else t)[:5]


def stems(s):
    """Crude stemming so 'blonde'/'blond', 'twinks'/'twink' count as the same word ('ass' stays 'ass')."""
    return {stem(t) for t in tokens(s)}


def dot(mat, vec):
    """mat @ vec with Accelerate's spurious FP-exception warnings silenced; falls back to float64 on NaN."""
    with np.errstate(all="ignore"):
        out = mat @ vec
    if np.isnan(out).any():
        out = mat.astype(np.float64) @ np.asarray(vec, dtype=np.float64)
    return out


def ngrams(toks, nmax=4):
    for n in range(1, nmax + 1):
        for i in range(len(toks) - n + 1):
            yield " ".join(toks[i:i + n])


class GoonBot:
    """Query -> matching site pages (via embeddings) -> fetch their cards -> rerank cards by the query."""

    def __init__(self, db=None, fetcher=None, embedder=None, verbose=False):
        self.db = db or DB()
        self.fetcher = fetcher or Fetcher()
        self._embedder = embedder
        self.verbose = verbose
        self._coll = None
        self._vid = None
        self._lock = threading.Lock()

    # ---- plumbing --------------------------------------------------------------------------
    def log(self, *a):
        if self.verbose:
            print("[goonbot]", *a, file=sys.stderr, flush=True)

    @property
    def embedder(self):
        if self._embedder is None:
            self.log("loading embedding model", config.EMBED_MODEL)
            t = time.time()
            self._embedder = Embedder()
            self.log("model ready on %s in %.1fs" % (self._embedder.device, time.time() - t))
        return self._embedder

    # ---- taxonomy ----------------------------------------------------------------------------
    def refresh_taxonomy(self, embed=True, progress=True):
        r = self.fetcher.get(SITEMAP_INDEX)
        subs = parse_sitemap(r.text)
        self.log("sitemap index lists %d sub-sitemaps" % len(subs))
        resps = self.fetcher.get_many(subs)
        rows = {}
        for sub, resp in zip(subs, resps):
            if isinstance(resp, Exception) or resp.status_code != 200:
                self.log("skipping", sub, resp)
                continue
            ranked = "item-collection-order" in sub or "terms/ranking" in sub
            n = 0
            for i, u in enumerate(parse_sitemap(resp.text)):
                c = collection_from_url(u)
                if not c:
                    continue
                n += 1
                c["rank"] = i if ranked else None
                prev = rows.get(c["url"])
                if prev is None or (c["rank"] is not None and (prev["rank"] is None or c["rank"] < prev["rank"])):
                    rows[c["url"]] = c
            self.log("%s: %d listings" % (sub.split(".com/")[1], n))
        self.db.upsert_collections(list(rows.values()))
        self.log("taxonomy: %d collections" % len(rows))
        self._coll = None
        if embed:
            self.embed_collections(progress=progress)
        return len(rows)

    def embed_collections(self, progress=True, batch=256):
        colls = [c for c in self.db.collections() if c["kind"] not in ("source", "search")]
        model = config.EMBED_MODEL
        hashes = {c["url"]: text_hash(c["name"]) for c in colls}
        have = self.db.vector_hashes("collection", model, list(hashes))
        todo = [c for c in colls if have.get(c["url"]) != hashes[c["url"]]]
        self.log("embedding %d collection names (%d already embedded)" % (len(todo), len(colls) - len(todo)))
        for i in range(0, len(todo), 2048):
            chunk = todo[i:i + 2048]
            vecs = self.embedder.encode_docs([c["name"] for c in chunk], batch_size=batch, progress=progress)
            self.db.put_vectors("collection", model, [(c["url"], hashes[c["url"]], v) for c, v in zip(chunk, vecs)])
            self.log("embedded %d/%d" % (min(i + 2048, len(todo)), len(todo)))
        self._coll = None

    def _coll_index(self):
        if self._coll is None:
            ids, mat = self.db.load_vectors("collection", config.EMBED_MODEL)
            meta = {c["url"]: c for c in self.db.collections()}
            name_map = {}
            for c in meta.values():  # ordered by site rank, so the first owner of a phrase is the most popular
                if c["kind"] in ("source", "search"):
                    continue
                key = " ".join(tokens(c["name"]))
                if key and key not in name_map:
                    name_map[key] = c["url"]
            self._coll = (ids, mat, meta, name_map)
        return self._coll

    def match_collections(self, query, k=None):
        """Pick the site pages (categories / pornstars / canned terms) most likely to hold the video.

        Three passes, most specific first: exact name hits; each 1-3 word phrase of the query matched
        to its closest page name (so every concept in the query gets covered); then the whole query
        semantically. Page names are matched symmetrically (query embedded like a name, no prompt).
        """
        k = k or config.MAX_COLLECTIONS
        ids, mat, meta, name_map = self._coll_index()
        if not ids:
            return []
        qt = tokens(query)
        grams = list(ngrams(qt, 3))
        vecs = self.embedder.encode_docs([query] + grams)
        sims = dot(mat, vecs.T)              # (collections, 1 + grams)
        full = sims[:, 0]
        pos = {u: i for i, u in enumerate(ids)}
        picked, seen = [], []

        def add(url, sim, via):
            c = meta.get(url)
            if not c or len(picked) >= k:
                return False
            tk = stems(c["name"])
            for s in seen:  # skip near-duplicates like "blonde twink" vs "blond twinks"
                inter = len(tk & s)
                if inter and inter / len(tk | s) >= 0.6:
                    return False
            seen.append(tk)
            picked.append(dict(url=url, kind=c["kind"], name=c["name"], sim=round(float(sim), 3),
                               direct=via == "exact", via=via, count=c.get("count")))
            return True

        # 1. exact name hits: a pornstar's name, a category, a canned term typed verbatim
        hits = [(len(g.split()), g, name_map[g]) for g in grams if g in name_map and (len(g.split()) >= 2 or len(qt) == 1)]
        for _, g, u in sorted(hits, reverse=True):
            add(u, full[pos[u]] if u in pos else 1.0, "exact")
        covered = set().union(*(stems(p["name"]) for p in picked)) if picked else set()

        # 2. every phrase of the query -> its closest page name, longest phrases first
        for j in sorted(range(len(grams)), key=lambda j: -len(grams[j].split())):
            g = grams[j]
            n = len(g.split())
            if n == 1 and (stems(g) <= covered or g in NUMBERS or g.isdigit()):
                continue  # "alex" when "alex mecum" already matched exactly; "two" -> "three"
            col = sims[:, j + 1]
            for i in np.argsort(-col)[:5]:
                s, c = float(col[i]), meta.get(ids[i])
                if c is None or s < (config.GRAM_MIN_SIM_1 if n == 1 else config.GRAM_MIN_SIM_N):
                    break
                if any(p["url"] == ids[i] for p in picked):
                    break  # this phrase is already covered; don't fall through to a worse page
                if n == 1 and c["kind"] == "term" and (c["rank"] is None or c["rank"] > config.TERM_MAX_RANK):
                    continue  # "room", "two", "happy": real terms on the site, useless as a page to open
                if n > 1 and s < config.GRAM_SURE_SIM and not (stems(g) & stems(c["name"])):
                    continue  # "two twins" -> "stepsister": semantically near, lexically unrelated
                if add(ids[i], s, g):
                    break

        # 3. whole-query semantic fill
        order = np.argsort(-full)
        floor = max(config.COLL_MIN_SIM, float(full[order[0]]) - config.COLL_REL_DROP)
        for i in order[:300]:
            if len(picked) >= k or full[i] < floor:
                break
            add(ids[i], full[i], "semantic")
        return picked

    # ---- fetching ----------------------------------------------------------------------------
    def fetch_collection_pages(self, targets, pages=1, max_age=6 * 3600):
        """Fetch pages 1..pages of each target listing, store the cards, embed new ones. Returns fresh ids."""
        now = time.time()
        jobs = []
        for t in targets:
            c = self.db.collection(t["url"])
            for pg in range(1, pages + 1):
                if c and c["pages_fetched"] >= pg and c["last_fetched"] and now - c["last_fetched"] < max_age:
                    continue
                jobs.append((t, pg, t["url"] + ("?page=%d" % pg if pg > 1 else "")))
        if not jobs:
            return set()
        t0 = time.time()
        resps = self.fetcher.get_many([j[2] for j in jobs])
        self.log("fetched %d pages in %.1fs" % (len(jobs), time.time() - t0))
        fresh, marks = set(), []
        for (t, pg, u), r in zip(jobs, resps):
            if isinstance(r, Exception) or r is None:
                self.log("fetch failed:", u, r)
                continue
            if r.status_code != 200:
                self.log("status %s for %s" % (r.status_code, u))
                continue
            data = parse_listing(r.text)
            if data["count"] is None and not data["cards"]:
                self.log("not a listing page (blocked, or the site changed?): %s" % u)
                continue
            if not self.db.collection(t["url"]):
                self.db.upsert_collections([dict(url=t["url"], kind=t["kind"], name=t["name"], rank=None)])
            if data["count"] == 0:  # the site pads an empty result with 120 unrelated popular videos
                self.log("no hits on the site for %r; ignoring its fallback page" % t["name"])
                self.db.mark_collection_fetched(t["url"], data["name"], 0, pg)
                t["count"] = 0
                continue
            self.db.upsert_videos(data["cards"], t["url"], pg)
            marks.append((t["url"], data["name"], data["count"], pg))
            t["count"] = data["count"]
            fresh.update(c["id"] for c in data["cards"])
            self.log("%-40s p%d  %3d cards  (site total: %s)" % (t["url"][:40], pg, len(data["cards"]), data["count"]))
        n = self.ensure_video_vectors(fresh)
        for m in marks:  # only now is the page truly done: its videos are stored AND searchable
            self.db.mark_collection_fetched(*m)
        self.log("embedded %d new/changed videos" % n)
        return fresh

    # ---- video vectors -----------------------------------------------------------------------
    def _vid_index(self):
        n = self.db.vector_count("video", config.EMBED_MODEL)
        if self._vid is None or n != len(self._vid["ids"]):  # first use, or another process (crawl) grew it
            ids, mat = self.db.load_vectors("video", config.EMBED_MODEL)
            self._vid = dict(ids=ids, mat=mat, pos={v: i for i, v in enumerate(ids)})
        return self._vid

    def ensure_video_vectors(self, ids):
        ids = list(ids)
        if not ids:
            return 0
        model = config.EMBED_MODEL
        texts = self.db.video_doc_texts(ids)
        hashes = {i: text_hash(t) for i, t in texts.items()}
        have = self.db.vector_hashes("video", model, list(hashes))
        todo = [i for i in hashes if have.get(i) != hashes[i]]
        if not todo:
            return 0
        idx = self._vid_index()  # sync with the table before we add to it
        vecs = self.embedder.encode_docs([texts[i] for i in todo], batch_size=128)
        self.db.put_vectors("video", model, [(i, hashes[i], v) for i, v in zip(todo, vecs)])
        new_ids, new_vecs = [], []
        for i, v in zip(todo, vecs):
            if i in idx["pos"]:
                idx["mat"][idx["pos"][i]] = v
            else:
                new_ids.append(i)
                new_vecs.append(v)
        if new_ids:
            block = np.stack(new_vecs)
            idx["mat"] = block if idx["mat"].size == 0 else np.vstack([idx["mat"], block])
            for i in new_ids:
                idx["pos"][i] = len(idx["ids"])
                idx["ids"].append(i)
        return len(todo)

    # ---- search ------------------------------------------------------------------------------
    def search(self, query, pages=1, limit=40, local_only=False, site_search=True):
        self.embedder  # one-off model load stays out of the timing
        with self._lock:
            return self._search(query, pages, limit, local_only, site_search)

    def _search(self, query, pages, limit, local_only, site_search):
        t0 = time.time()
        query = " ".join(query.split())
        qvec = self.embedder.encode_query(query)
        targets = self.match_collections(query)
        if site_search:
            targets.append(dict(url="/search/" + quote(query, safe=""), kind="search", name=query, sim=None, direct=False, via="site search", count=None))
        self.log("pages: " + "; ".join("%s:%s%s" % (t["kind"], t["name"], "" if t["sim"] is None else " (%.2f via %s)" % (t["sim"], t.get("via"))) for t in targets))
        fresh = set() if local_only else self.fetch_collection_pages(targets, pages=pages)
        for t in targets:  # pages served from the fetch cache still get their last known size
            if t.get("count") is None:
                c = self.db.collection(t["url"])
                t["count"] = c["count"] if c else None
        idx = self._vid_index()
        base = dict(query=query, collections=targets, took=round(time.time() - t0, 2), fresh=len(fresh), indexed=len(idx["ids"]))
        if not idx["ids"]:
            return dict(base, results=[])

        sims = dot(idx["mat"], qvec)
        n = min(len(sims), max(limit * 6, 300))
        top = np.argpartition(-sims, n - 1)[:n]
        top = top[np.argsort(-sims[top])]
        rows = self.db.videos([idx["ids"][i] for i in top])
        qtok = stems(query)
        coll_w = {t["url"]: (0.8 if t["sim"] is None else t["sim"]) for t in targets}
        scored = []
        for i in top:
            vid = idx["ids"][i]
            r = rows.get(vid)
            if not r:
                continue
            sim = float(sims[i])
            kw = len(qtok & stems(r["title"])) / len(qtok) if qtok else 0.0
            ch = max([coll_w.get(u, 0.0) for u, _, _ in r["collections"]] + [0.0])
            rating = (r["score"] if r["score"] is not None else 60) / 100.0
            final = config.W_SIM * sim + config.W_KEYWORD * kw + config.W_COLLECTION * ch + config.W_RATING * rating
            r["sim"], r["final"], r["fresh"] = round(sim, 3), round(final, 3), vid in fresh
            r["url"] = (config.BASE_URL + r["out_url"]) if r["out_url"] else None
            r["collections"] = [dict(url=u, kind=k, name=nm) for u, k, nm in r["collections"] if k != "source"]
            scored.append(r)
        scored.sort(key=lambda r: -r["final"])
        results, by_title = [], {}
        for r in scored:  # same video mirrored on several sources: keep the best, list the others
            key = " ".join(tokens(r["title"])) or r["id"]
            if key in by_title:
                by_title[key]["alts"].append(dict(id=r["id"], url=r["url"], source_name=r["source_name"], quality=r["quality"], duration=r["duration"]))
                continue
            r["alts"] = []
            by_title[key] = r
            results.append(r)
            if len(results) >= limit:
                break
        base["took"] = round(time.time() - t0, 2)
        return dict(base, results=results)

    # ---- crawl -------------------------------------------------------------------------------
    def crawl(self, kinds=("cat", "pornstar"), top=100, pages=1, max_age=7 * 86400):
        colls = self.db.collections(kinds=list(kinds), limit=top)
        self.log("crawling up to %d collections x %d pages" % (len(colls), pages))
        total = 0
        for i in range(0, len(colls), 8):
            batch = [dict(url=c["url"], kind=c["kind"], name=c["name"]) for c in colls[i:i + 8]]
            total += len(self.fetch_collection_pages(batch, pages=pages, max_age=max_age))
            self.log("progress %d/%d collections, %d videos indexed" % (min(i + 8, len(colls)), len(colls), self.db.video_count()))
        return total

    def reindex(self):
        """Re-embed every video whose embedding text changed (new tags, new doc-text rules, new model)."""
        ids = self.db.video_ids()
        n = 0
        for i in range(0, len(ids), 4096):
            n += self.ensure_video_vectors(ids[i:i + 4096])
            self.log("reindex %d/%d (%d re-embedded)" % (min(i + 4096, len(ids)), len(ids), n))
        return n

    def stats(self):
        by_kind = {r[0]: r[1] for r in self.db.conn.execute("SELECT kind, COUNT(*) FROM collections GROUP BY kind")}
        return dict(model=config.EMBED_MODEL, collections=by_kind, videos=self.db.video_count(),
                    collection_vectors=self.db.vector_count("collection", config.EMBED_MODEL),
                    video_vectors=self.db.vector_count("video", config.EMBED_MODEL), db=str(config.DB_PATH))
