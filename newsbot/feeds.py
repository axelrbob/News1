"""Collecte des flux RSS/Atom."""
import calendar
import hashlib
import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; newsbot/1.0; RSS reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}
TRACKING = re.compile(r"^(utm_|xtor|at_|fbclid|gclid|ref$|cmp$|mc_)", re.I)
TAGS = re.compile(r"<[^>]+>")


def clean_link(url):
    """Retire les paramètres de suivi pour dédoublonner un même article."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def item_id(link, title):
    return hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:16]


def strip_html(text, limit=400):
    text = html.unescape(TAGS.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fetch_source(source, max_age_h=36):
    """Renvoie (source, liste d'articles, erreur)."""
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:  # flux en panne : on continue avec les autres
        return source, [], str(exc)[:200]
    if not feed.entries:
        return source, [], "flux vide ou illisible"

    now = time.time()
    items = []
    for entry in feed.entries[:60]:
        title = strip_html(entry.get("title"), 300)
        link = entry.get("link") or ""
        if not title or not link:
            continue
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        ts = calendar.timegm(parsed) if parsed else now
        ts = min(ts, now)  # certaines dates sont dans le futur
        if now - ts > max_age_h * 3600:
            continue
        link = clean_link(link)
        items.append({
            "id": item_id(link, title),
            "title": title,
            "summary": strip_html(entry.get("summary") or entry.get("description")),
            "link": link,
            "ts": ts,
            "source": source["name"],
            "region": source.get("region", "international"),
            "default_cat": source.get("cat", "monde"),
            "poids": source.get("poids", 1),
            "digest_only": bool(source.get("digest_only")),
            "science": bool(source.get("science")),
        })
    return source, items, None


def fetch_all(sources, workers=16):
    articles, errors = [], {}
    with ThreadPoolExecutor(workers) as pool:
        for source, items, err in pool.map(fetch_source, sources):
            if err:
                errors[source["name"]] = err
            articles.extend(items)
    log.info("%d articles récupérés, %d flux en erreur", len(articles), len(errors))
    for name, err in errors.items():
        log.warning("flux en erreur : %s (%s)", name, err)
    return articles, errors
