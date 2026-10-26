import argparse
import json
import sys

from . import config


def _fmt_count(n):
    return "{:,}".format(n) if isinstance(n, int) else "?"


def cmd_init(args, bot):
    n = bot.refresh_taxonomy(embed=not args.no_embed)
    print("taxonomy ready: %d collections. stats: %s" % (n, json.dumps(bot.stats())))


def cmd_search(args, bot):
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    query = " ".join(args.query)
    res = bot.search(query, pages=args.pages, limit=args.limit, local_only=args.local)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    con = Console()
    con.print("[bold]pages searched:[/] " + "   ".join(
        "[cyan]%s[/]:%s%s%s" % (t["kind"], escape(t["name"]), "" if t["sim"] is None else " [dim]%.2f[/]" % t["sim"],
                                " [dim](%s)[/]" % _fmt_count(t["count"]) if t.get("count") else "")
        for t in res["collections"]))
    tbl = Table(show_lines=False, header_style="bold", title="%d results · %.1fs · %d cards fetched · %s videos indexed" % (
        len(res["results"]), res["took"], res["fresh"], _fmt_count(res["indexed"])))
    for col, just in (("#", "right"), ("match", "right"), ("len", "right"), ("q", "left"), ("👍", "right"), ("source", "left"), ("title", "left")):
        tbl.add_column(col, justify=just, no_wrap=col != "title")
    for i, v in enumerate(res["results"], 1):
        tbl.add_row(str(i), "%.2f" % v["final"], v.get("duration") or "", v.get("quality") or "",
                    "" if v.get("score") is None else "%d%%" % v["score"], escape(v.get("source_name") or ""),
                    escape(v["title"]) + (" [dim](+%d mirror)[/]" % len(v["alts"]) if v.get("alts") else ""))
    con.print(tbl)
    if not args.no_links:
        for i, v in enumerate(res["results"], 1):
            con.print("[dim]%2d.[/] %s" % (i, escape(v["url"] or "(no link)")), soft_wrap=True)
            for a in v.get("alts") or []:
                con.print("    [dim]also on %s:[/] %s" % (escape(a.get("source_name") or "?"), escape(a.get("url") or "(no link)")), soft_wrap=True)


def cmd_crawl(args, bot):
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    n = bot.crawl(kinds=kinds, top=args.top, pages=args.pages)
    print("crawl done: %d cards seen, %d videos indexed" % (n, bot.db.video_count()))


def cmd_serve(args, bot):
    from .web import serve
    serve(args.host, args.port, bot, open_browser=not args.no_open)


def cmd_reindex(args, bot):
    n = bot.reindex()
    print("re-embedded %d of %d videos" % (n, bot.db.video_count()))


def cmd_stats(args, bot):
    print(json.dumps(bot.stats(), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="goonbot", description="Describe the video, get the video. Semantic search over gaymaletube.")
    p.add_argument("-v", "--verbose", action="store_true", help="log what is happening")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    s = sub.add_parser("init", help="pull the site's taxonomy (categories, pornstars, search terms) and embed it")
    s.add_argument("--no-embed", action="store_true", help="only store the taxonomy, skip embedding")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("search", help="find videos from a description, a name, or a half-remembered title")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", "--limit", type=int, default=25, help="results to show (default 25)")
    s.add_argument("-p", "--pages", type=int, default=1, help="listing pages to pull per matched site page (default 1)")
    s.add_argument("--local", action="store_true", help="search only what is already cached, no network")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-links", action="store_true", help="do not print the (long) video links")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("crawl", help="prefetch listing pages so later searches are faster and deeper")
    s.add_argument("--kinds", default="cat,pornstar", help="comma list of cat,pornstar,term (default cat,pornstar)")
    s.add_argument("--top", type=int, default=100, help="how many of the most popular collections (default 100)")
    s.add_argument("--pages", type=int, default=1, help="pages per collection, 120 videos each (default 1)")
    s.set_defaults(fn=cmd_crawl)

    s = sub.add_parser("serve", help="start the web UI and open it in your browser")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--no-open", action="store_true", help="do not open a browser window")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("reindex", help="re-embed cached videos after changing the model or tagging rules")
    s.set_defaults(fn=cmd_reindex)

    s = sub.add_parser("stats", help="what is in the local index")
    s.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    from .search import GoonBot
    bot = GoonBot(verbose=args.verbose or args.cmd in ("init", "crawl", "reindex"))
    try:
        args.fn(args, bot)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
