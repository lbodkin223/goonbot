import hashlib
import sqlite3
import time

import numpy as np

from .config import DATA_DIR, DB_PATH, DOC_MAX_TAGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  url TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  rank INTEGER,
  count INTEGER,
  pages_fetched INTEGER NOT NULL DEFAULT 0,
  last_fetched REAL
);
CREATE INDEX IF NOT EXISTS collections_kind ON collections(kind, rank);
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  out_url TEXT,
  thumb TEXT,
  score INTEGER,
  quality TEXT,
  duration TEXT,
  seconds INTEGER,
  source TEXT,
  source_name TEXT,
  age TEXT,
  paid INTEGER NOT NULL DEFAULT 0,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS video_collections (
  video_id TEXT NOT NULL,
  url TEXT NOT NULL,
  page INTEGER,
  PRIMARY KEY (video_id, url)
);
CREATE INDEX IF NOT EXISTS vc_url ON video_collections(url);
CREATE TABLE IF NOT EXISTS vectors (
  kind TEXT NOT NULL,
  id TEXT NOT NULL,
  model TEXT NOT NULL,
  hash TEXT NOT NULL,
  vec BLOB NOT NULL,
  PRIMARY KEY (kind, id, model)
);
"""


def _chunks(seq, n=800):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def text_hash(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


class DB:
    def __init__(self, path=DB_PATH):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ---- collections -------------------------------------------------------------------------
    def upsert_collections(self, rows):
        self.conn.executemany(
            """INSERT INTO collections(url, kind, name, rank) VALUES (:url, :kind, :name, :rank)
               ON CONFLICT(url) DO UPDATE SET kind=excluded.kind, name=excluded.name,
               rank=COALESCE(excluded.rank, collections.rank)""",
            [dict(rank=None, **{k: v for k, v in r.items() if k != "rank"}) | {"rank": r.get("rank")} for r in rows],
        )
        self.conn.commit()

    def mark_collection_fetched(self, url, name, count, page):
        self.conn.execute(
            """UPDATE collections SET count=COALESCE(?, count), pages_fetched=MAX(pages_fetched, ?),
               last_fetched=? WHERE url=?""",
            (count, page, time.time(), url),
        )
        self.conn.commit()

    def collections(self, kinds=None, limit=None):
        q = "SELECT * FROM collections"
        args = []
        if kinds:
            q += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        q += " ORDER BY (rank IS NULL), rank, url"
        if limit:
            q += " LIMIT %d" % int(limit)
        return [dict(r) for r in self.conn.execute(q, args)]

    def collection(self, url):
        r = self.conn.execute("SELECT * FROM collections WHERE url=?", (url,)).fetchone()
        return dict(r) if r else None

    # ---- videos ------------------------------------------------------------------------------
    def upsert_videos(self, cards, collection_url, page):
        now = time.time()
        self.conn.executemany(
            """INSERT INTO videos(id, title, out_url, thumb, score, quality, duration, seconds, source,
                                  source_name, age, paid, first_seen, last_seen)
               VALUES (:id, :title, :out_url, :thumb, :score, :quality, :duration, :seconds, :source,
                       :source_name, :age, :paid, :now, :now)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, out_url=excluded.out_url,
                 thumb=COALESCE(excluded.thumb, videos.thumb), score=COALESCE(excluded.score, videos.score),
                 quality=COALESCE(excluded.quality, videos.quality), duration=COALESCE(excluded.duration, videos.duration),
                 seconds=COALESCE(excluded.seconds, videos.seconds), source=COALESCE(excluded.source, videos.source),
                 source_name=COALESCE(excluded.source_name, videos.source_name), age=COALESCE(excluded.age, videos.age),
                 paid=excluded.paid, last_seen=excluded.last_seen""",
            [dict(c, now=now) for c in cards],
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO video_collections(video_id, url, page) VALUES (?, ?, ?)",
            [(c["id"], collection_url, page) for c in cards],
        )
        self.conn.commit()

    def videos(self, ids):
        """Rows for the given ids, each with a 'collections' list of (url, kind, name)."""
        out = {}
        for chunk in _chunks(ids):
            ph = ",".join("?" * len(chunk))
            for r in self.conn.execute("SELECT * FROM videos WHERE id IN (%s)" % ph, chunk):
                d = dict(r)
                d["collections"] = []
                out[d["id"]] = d
            for r in self.conn.execute(
                """SELECT vc.video_id, c.url, c.kind, c.name FROM video_collections vc
                   JOIN collections c ON c.url = vc.url WHERE vc.video_id IN (%s)""" % ph, chunk):
                if r["video_id"] in out:
                    out[r["video_id"]]["collections"].append((r["url"], r["kind"], r["name"]))
        return out

    def video_doc_texts(self, ids):
        """Text that gets embedded for each video: title plus the site pages it was seen on."""
        rows = self.videos(ids)
        texts = {}
        for vid, r in rows.items():
            names = sorted({n for _, k, n in r["collections"] if k in ("cat", "pornstar", "term")})[:DOC_MAX_TAGS]
            texts[vid] = r["title"] + (" | " + ", ".join(names) if names else "")
        return texts

    def video_ids(self):
        return [r[0] for r in self.conn.execute("SELECT id FROM videos")]

    def video_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    # ---- vectors -----------------------------------------------------------------------------
    def vector_hashes(self, kind, model, ids):
        out = {}
        for chunk in _chunks(ids):
            ph = ",".join("?" * len(chunk))
            for r in self.conn.execute(
                "SELECT id, hash FROM vectors WHERE kind=? AND model=? AND id IN (%s)" % ph, [kind, model] + chunk):
                out[r["id"]] = r["hash"]
        return out

    def put_vectors(self, kind, model, rows):
        """rows: iterable of (id, hash, np.ndarray)."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO vectors(kind, id, model, hash, vec) VALUES (?, ?, ?, ?, ?)",
            [(kind, i, model, h, np.asarray(v, dtype=np.float16).tobytes()) for i, h, v in rows],
        )
        self.conn.commit()

    def load_vectors(self, kind, model):
        ids, vecs = [], []
        for r in self.conn.execute("SELECT id, vec FROM vectors WHERE kind=? AND model=?", (kind, model)):
            ids.append(r["id"])
            vecs.append(np.frombuffer(r["vec"], dtype=np.float16))
        if not ids:
            return [], np.zeros((0, 0), dtype=np.float32)
        return ids, np.stack(vecs).astype(np.float32)

    def vector_count(self, kind, model):
        return self.conn.execute("SELECT COUNT(*) FROM vectors WHERE kind=? AND model=?", (kind, model)).fetchone()[0]
