# goonbot

Describe the video, get the video. A natural-language finder for well its self explanitory, for when you
remember "blonde boy in gym" or half an actor's name but the site's own search gives you nothing.

Runs entirely on your Mac. No API keys. The "LLM" is **Qwen3-Embedding-0.6B** (Alibaba, open
weights, Apache-2.0), an embedding model: it turns text into vectors and compares them, so it has no
refusals or content filters to fight. It runs on the Apple GPU via PyTorch MPS.

## How it works

The site has ~12.7 million videos and no video sitemap, so it is never scraped whole. Instead:

1. `init` pulls the site's sitemaps: ~2,700 categories, ~4,800 pornstars and ~50,000 canned search
   terms, and embeds every name once (about 4 minutes, done once).
2. `search "blonde boy in gym"` embeds your query, finds the site pages whose names are closest in
   meaning (e.g. `term: blonde twink gym`, `cat: gym`, `cat: blonde`), plus any exact name hit
   (type a pornstar's name and their page is used directly), plus the site's own keyword search.
3. It fetches page 1 of each of those (6 to 8 requests, ~2 s), parses the 120 cards per page into a
   local SQLite database, embeds each title together with the pages it was seen on, and reranks
   everything in the local index against your query. Top results come back with thumbnail, length,
   HD/4K, rating, source and the link.
4. Every search grows the local index, so later searches get faster and deeper. `crawl` prefetches
   popular categories and pornstars in bulk.

The site is behind Cloudflare; requests go through `curl_cffi` with a Chrome TLS fingerprint, which
passes the challenge without a browser.

## Install

Python 3.9 or newer. The embedding model runs on an Apple GPU (MPS), an NVIDIA GPU (CUDA) or CPU;
the first run downloads it (~1.1 GB) into `~/.cache/huggingface`.

```bash
pipx install git+https://github.com/lbodkin223/goonbot.git
```

or, without pipx:

```bash
pip install git+https://github.com/lbodkin223/goonbot.git
```

Either gives you a `goonbot` command. From a clone, `pip install -e .` does the same and picks up
your edits. Then build the taxonomy index once (about 4 minutes on an M-series Mac):

```bash
goonbot init
```

Everything it stores goes in `~/.goonbot/` (set `GOONBOT_DATA` to put it elsewhere). Delete that
folder to start over.

## Use

```bash
goonbot search blonde boy in gym
goonbot search "alex mecum" locker room
goonbot search that one with the two twins and the plumber -n 40 -p 2
goonbot search hairy daddy --local        # instant, cached index only
goonbot serve                             # web UI with thumbnails at http://127.0.0.1:8787/
goonbot crawl --kinds cat,pornstar --top 300 --pages 2   # grow the index in bulk
goonbot reindex                           # re-embed cached videos after changing the model
goonbot stats
```

`crawl` can run while `serve` is up: the server notices the index grew and reloads it on the next search.

Add `-v` before the subcommand to see which pages it chose and why:

```bash
goonbot -v search blonde boy in gym
```

Search options: `-n` results to show, `-p` listing pages to pull per matched site page (each page is
120 videos; the site caps listings at 100 pages), `--local` no network, `--json` raw output.

A page that was pulled in the last 6 hours is not pulled again, so repeating or refining a query is
fast and only new pages cost network time. The first `search` in a process spends ~6 s loading the
model; the web server loads it once.

## Tuning

Everything lives in `goonbot/config.py`:

- `EMBED_MODEL`: any sentence-transformers model. Smaller and faster: `BAAI/bge-small-en-v1.5`.
  Changing the model means re-embedding (`init` again; video vectors re-embed lazily).
- `MAX_COLLECTIONS`, `COLL_MIN_SIM`, `COLL_REL_DROP`: how many site pages a query fans out to and
  how close a match must be.
- `W_SIM`, `W_KEYWORD`, `W_COLLECTION`, `W_RATING`: final ranking weights (semantic similarity,
  exact word overlap with the title, whether the video sat on a matched page, thumbs-up %).
- `CONCURRENCY`, `REQUEST_DELAY`: be polite to the site.

## Layout

```
pyproject.toml   package metadata; `goonbot` console script
goonbot/
  config.py   knobs
  fetch.py    Cloudflare-passing HTTP client, paced and parallel
  parse.py    listing-page and sitemap parsers (lxml)
  db.py       SQLite: collections, videos, which video was seen on which page, vectors
  embed.py    sentence-transformers wrapper (MPS, fp16)
  search.py   the pipeline: taxonomy match -> fetch -> embed -> rerank; crawl; stats
  cli.py      command line
  web.py      FastAPI web UI
```

## Ideas for later

- Query rewriting with a small local chat model (Qwen3 via Ollama) to expand slang into the site's
  vocabulary before matching.
- Pull the video page on each source site (xHamster, etc.) for the top hits to get their tags and
  pornstar lists, and fold those into the embedding text.
- Other sites: `parse.py` and the sitemap mapping are the only site-specific parts.
