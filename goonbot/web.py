from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from . import config

HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>goonbot</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}
body{margin:0;background:#0f0f12;color:#e8e8ea;font:15px/1.4 system-ui,-apple-system,sans-serif}
header{position:sticky;top:0;z-index:5;background:#0f0f12e6;backdrop-filter:blur(8px);padding:12px 16px;border-bottom:1px solid #26262b;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0 6px 0 0;letter-spacing:.02em}
input[type=search]{flex:1;min-width:220px;background:#1a1a1f;border:1px solid #33333a;color:#fff;padding:10px 12px;border-radius:8px;font-size:16px}
button{background:#e2453c;color:#fff;border:0;padding:10px 16px;border-radius:8px;font-weight:600;cursor:pointer;font-size:15px}
label.opt{color:#aaa;font-size:13px;display:flex;gap:4px;align-items:center;white-space:nowrap}
#status{padding:8px 16px;color:#999;font-size:13px;min-height:1.4em}
#chips{padding:0 16px 10px;display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#1f1f25;border:1px solid #33333a;border-radius:999px;padding:3px 10px;font-size:12px;color:#ccc;text-decoration:none}
.chip b{color:#fff;font-weight:600}
.chip.direct{border-color:#e2453c}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;padding:0 16px 40px}
.card{background:#16161a;border-radius:10px;overflow:hidden;border:1px solid #232328;display:flex;flex-direction:column}
.card a.th{display:block;position:relative;aspect-ratio:16/9;background:#000}
.card img{width:100%;height:100%;object-fit:cover;display:block}
.badge{position:absolute;right:6px;bottom:6px;background:#000c;color:#fff;font-size:12px;padding:2px 6px;border-radius:4px}
.rating{position:absolute;left:6px;bottom:6px;background:#000c;color:#7ee787;font-size:12px;padding:2px 6px;border-radius:4px}
.card .t{padding:8px 10px 2px;font-weight:600;font-size:14px;line-height:1.3}
.card .t a{color:#fff;text-decoration:none}
.card .m{padding:2px 10px 6px;color:#888;font-size:12px;display:flex;justify-content:space-between;gap:6px}
.card .m a{color:#aaa}
.card .tags{padding:0 10px 10px;color:#666;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.empty{padding:40px 16px;color:#777;text-align:center}
</style></head><body>
<header><h1>goonbot</h1>
<input id="q" type="search" placeholder="describe it… e.g. blonde boy in gym, or a name, or half a title" autofocus>
<button id="go">find</button>
<label class="opt"><input id="deep" type="checkbox"> deep (2 pages each)</label>
<label class="opt"><input id="local" type="checkbox"> cached only</label>
</header>
<div id="status"></div><div id="chips"></div><div id="grid"></div>
<script>
const BASE = "__BASE__";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function run(){
  const q = $('#q').value.trim(); if(!q) return;
  history.replaceState(null, '', '?q=' + encodeURIComponent(q));
  $('#status').textContent = 'searching…'; $('#grid').innerHTML = ''; $('#chips').innerHTML = '';
  const p = new URLSearchParams({q, pages: $('#deep').checked ? 2 : 1, local: $('#local').checked ? 1 : 0, limit: 60});
  try {
    const r = await fetch('/api/search?' + p); const d = await r.json();
    $('#status').textContent = `${d.results.length} results · ${d.took}s · ${d.fresh} cards pulled · ${Number(d.indexed).toLocaleString()} videos indexed locally`;
    $('#chips').innerHTML = d.collections.map(c => `<a class="chip ${c.direct ? 'direct' : ''}" title="${esc(c.via || '')}${c.sim != null ? ' · ' + c.sim.toFixed(2) : ''}" target="_blank" rel="noreferrer" href="${esc(BASE + c.url)}"><b>${esc(c.kind)}</b> ${esc(c.name)}${c.count ? ' · ' + Number(c.count).toLocaleString() : ''}</a>`).join('');
    if(!d.results.length){ $('#grid').innerHTML = '<div class="empty">nothing yet — try different words, or untick "cached only"</div>'; return; }
    $('#grid').innerHTML = d.results.map(v => `<div class="card">
      <a class="th" href="${esc(v.url || '#')}" target="_blank" rel="noreferrer"><img loading="lazy" src="${esc(v.thumb || '')}" alt="">
        ${v.score != null ? `<span class="rating">👍 ${v.score}%</span>` : ''}<span class="badge">${v.quality ? esc(v.quality) + ' ' : ''}${esc(v.duration || '')}</span></a>
      <div class="t"><a href="${esc(v.url || '#')}" target="_blank" rel="noreferrer">${esc(v.title)}</a></div>
      <div class="m"><span>${esc(v.source_name || '')}${v.alts && v.alts.length ? ' ' + v.alts.map(a => `· <a href="${esc(a.url || '#')}" target="_blank" rel="noreferrer">${esc(a.source_name || 'mirror')}</a>`).join(' ') : ''}</span><span>${esc(v.age || '')}</span><span title="match score">${v.final.toFixed(2)}</span></div>
      <div class="tags">${v.collections.map(c => esc(c.name)).join(' · ')}</div></div>`).join('');
  } catch(e){ $('#status').textContent = 'error: ' + e; }
}
$('#go').onclick = run;
$('#q').addEventListener('keydown', e => { if(e.key === 'Enter') run(); });
const init = new URLSearchParams(location.search).get('q'); if(init){ $('#q').value = init; run(); }
</script></body></html>"""


def make_app(bot):
    @asynccontextmanager
    async def lifespan(app):
        bot.embedder  # load the model once, up front
        yield

    app = FastAPI(title="goonbot", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTML.replace("__BASE__", config.BASE_URL)

    @app.get("/api/search")
    def api_search(q: str = Query(..., min_length=1), pages: int = 1, limit: int = 60, local: int = 0):
        return bot.search(q, pages=max(1, min(pages, 5)), limit=max(1, min(limit, 200)), local_only=bool(local))

    @app.get("/api/stats")
    def api_stats():
        return bot.stats()

    return app


def serve(host, port, bot):
    import uvicorn
    bot.verbose = True
    print("goonbot web UI: http://%s:%d/" % (host, port))
    uvicorn.run(make_app(bot), host=host, port=port, log_level="warning")
