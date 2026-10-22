import html as _html
import re
from urllib.parse import unquote

from lxml import html as LH

from .config import BASE_URL

SITEMAP_INDEX = "/sitemap.xml"


def _cls(name):
    return 'contains(concat(" ", normalize-space(@class), " "), " %s ")' % name


def _seconds(s):
    try:
        parts = [int(p) for p in s.strip().rstrip("+").split(":")]
    except ValueError:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def parse_listing(text):
    """Parse a category / pornstar / term / search listing page into {name, count, cards}."""
    doc = LH.fromstring(text)
    h1 = " ".join(t.strip() for t in doc.xpath("//h1//text()") if t.strip())
    m = re.match(r"^(.*?)\s*\(([\d,]+)\)\s*$", h1)
    if m:
        name, count = m.group(1).strip(), int(m.group(2).replace(",", ""))
    else:
        name, count = h1, None

    cards = []
    for c in doc.xpath("//div[@data-public-id]"):
        cls = c.get("class", "").split()
        link = c.xpath(".//a[%s]/@href" % _cls("item-title")) or c.xpath(".//a[%s]/@href" % _cls("item-link"))
        title = c.xpath(".//a[%s]/@title" % _cls("item-title")) or c.xpath(".//a[%s]/@title" % _cls("item-link"))
        thumb = c.xpath(".//img[%s]/@src" % _cls("item-image"))
        score = "".join(c.xpath(".//span[%s]//text()" % _cls("item-score"))).strip().rstrip("%")
        quality = duration = None
        badge = c.xpath(".//span[%s]" % _cls("badge"))
        if badge:
            b = badge[0]
            full = " ".join(" ".join(b.xpath(".//text()")).split())
            quality = " ".join(" ".join(b.xpath('./span[contains(@class, "font-bold")]//text()')).split()) or None
            duration = (full.replace(quality, "", 1) if quality else full).strip() or None
        src = c.xpath(".//a[%s]" % _cls("item-source"))
        source = src[0].get("href") if src else None
        source_name = "".join(src[0].xpath(".//text()")).strip() if src else None
        age = "".join(c.xpath(".//span[%s]//text()" % _cls("item-source"))).strip() or None
        title = _html.unescape(title[0]).strip() if title else ""
        if not title:
            continue
        cards.append(dict(
            id=c.get("data-public-id"),
            title=title,
            out_url=link[0] if link else None,
            thumb=thumb[0] if thumb else None,
            score=int(score) if score.isdigit() else None,
            quality=quality,
            duration=duration,
            seconds=_seconds(duration.split("/")[-1]) if duration else None,
            source=source,
            source_name=source_name,
            age=age,
            paid=int("paid" in cls),
        ))
    return dict(name=name, count=count, cards=cards)


def parse_sitemap(text):
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", text)


def collection_from_url(url):
    """Map a sitemap URL to a collection row, or None if it is not a browsable listing."""
    path = url.replace(BASE_URL, "")
    if path.startswith("/cat/"):
        kind, slug = "cat", path[len("/cat/"):]
    elif path.startswith("/pornstar/"):
        kind, slug = "pornstar", path[len("/pornstar/"):]
    elif path.startswith("/search/a/"):
        kind, slug = "term", path[len("/search/a/"):]
    elif path.startswith("/source/"):
        kind, slug = "source", path[len("/source/"):]
    else:
        return None
    slug = slug.strip("/")
    if not slug or (kind == "pornstar" and len(slug) == 1):  # /pornstar/a .. /pornstar/z are A-Z index pages
        return None
    name = unquote(slug) if kind == "term" else unquote(slug).replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return dict(url=path, kind=kind, name=name)
