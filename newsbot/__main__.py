"""Bot d'actualités : alertes immédiates + résumé du soir sur Telegram.

Usage :
    python -m newsbot run            un cycle : collecte, alertes, résumé si c'est l'heure
    python -m newsbot loop           tourne en continu (serveur, Raspberry Pi...)
    python -m newsbot digest         envoie le résumé maintenant
    python -m newsbot check          teste tous les flux
    python -m newsbot chatid         affiche l'identifiant de ta conversation Telegram
    python -m newsbot test           envoie un message de test
Option --dry-run : affiche les messages au lieu de les envoyer.
"""
import argparse
import gzip
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from .feeds import fetch_all
from .llm import Analyst
from .scoring import Scorer, corroboration, signature, similar
from .telegram import Telegram, esc

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(os.environ.get("NEWSBOT_STATE", ROOT / "data" / "state.json.gz"))
log = logging.getLogger("newsbot")

CAT_LABEL = {"france": "🇫🇷 France", "energie": "⚡ Énergie", "monde": "🌍 Monde"}
REGION_LABEL = {
    "france": "France", "europe": "Europe", "amerique-nord": "Amérique du Nord", "asie": "Asie",
    "chine": "Chine", "inde": "Inde", "japon-coree": "Japon/Corée", "moyen-orient": "Moyen-Orient",
    "afrique": "Afrique", "russie": "Russie", "amerique-latine": "Amérique latine",
    "international": "International",
}
WESTERN = {"france", "europe", "amerique-nord", "international"}
SCORE_ICON = {5: "🔴", 4: "🟠", 3: "🟡", 2: "⚪", 1: "⚪"}
TOPICS = [("urgent", "🔴 Urgent", 0xFB6F5F), ("france", "🇫🇷 France", 0x6FB9F0),
          ("energie", "⚡ Énergie", 0xFFD67E), ("monde", "🌍 Monde", 0x8EEE98),
          ("resume", "🌙 Résumé du soir", 0xCB86DB)]


# ---------------------------------------------------------------- état

def load_state():
    if STATE_FILE.exists():
        with gzip.open(STATE_FILE, "rt", encoding="utf-8") as f:
            return json.load(f)
    return {"items": {}, "alerts": [], "last_digest": None, "last_digest_ts": 0, "topics": {}, "initialized": False}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def load_yaml(name):
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- mise en forme

def title_of(item):
    return item.get("title_fr") or item["title"]


def line(item):
    extra = f" · +{item['echo'] - 1} médias" if item.get("echo", 1) > 1 else ""
    return (f"{SCORE_ICON[item['score']]} <b>{esc(title_of(item))}</b>\n"
            f"<i>{esc(item['source'])} · {REGION_LABEL.get(item['region'], item['region'])}{extra}</i>"
            f" · <a href=\"{esc(item['link'])}\">lire</a>")


def alert_text(item):
    cats = " · ".join(CAT_LABEL[c] for c in item["cats"])
    head = "🔴 <b>URGENT</b>" if item["score"] >= 5 else "🟠 <b>IMPORTANT</b>"
    original = f"\n<i>{esc(item['title'])}</i>" if item.get("title_fr") and item["title_fr"] != item["title"] else ""
    return (f"{head} · {cats}\n\n<b>{esc(title_of(item))}</b>{original}\n\n"
            f"{esc(item['source'])} ({REGION_LABEL.get(item['region'], item['region'])}) · note {item['score']}/5\n"
            f"<a href=\"{esc(item['link'])}\">Lire l'article</a>")


def dedupe(items):
    """Regroupe les articles qui parlent de la même chose, garde le mieux noté."""
    kept = []
    for it in sorted(items, key=lambda x: (-x["score"], -x["poids"], -x["ts"])):
        sig = signature(it["title"])
        for k in kept:
            if similar(sig, k["_sig"]):
                k["echo"] = k.get("echo", 1) + 1
                break
        else:
            kept.append({**it, "_sig": sig, "echo": 1})
    return kept


# ---------------------------------------------------------------- cycle principal

class Bot:
    def __init__(self, dry_run=False):
        self.config = load_yaml("config.yaml")
        self.sources = load_yaml("sources.yaml")["sources"]
        self.tz = ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))
        self.scorer = Scorer(self.config)
        self.analyst = Analyst(self.config)
        self.tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID"), dry_run)
        self.state = load_state()

    def topics(self):
        """Mode « onglets » : si la conversation est un groupe avec Sujets, un sujet par rubrique."""
        if self.state.get("topics"):
            return self.state["topics"]
        try:
            if not self.tg.is_forum():
                return {}
            self.state["topics"] = {key: self.tg.create_topic(name, color) for key, name, color in TOPICS}
            log.info("onglets créés : %s", self.state["topics"])
        except Exception as exc:
            log.warning("onglets indisponibles (%s) : envoi dans la conversation principale", exc)
            return {}
        return self.state["topics"]

    def run(self):
        now = time.time()
        articles, _ = fetch_all(self.sources)
        known = self.state["items"]
        fresh = list({a["id"]: a for a in articles if a["id"] not in known}.values())
        log.info("%d nouveaux articles", len(fresh))

        recent = [{"source": v["source"], "sig": set(v["sig"])} for v in known.values() if now - v["ts"] < 12 * 3600]
        recent += [{"source": a["source"], "sig": signature(a["title"])} for a in fresh]
        for a in fresh:
            a["cats"] = self.scorer.categorize(a)
            a["echo"] = corroboration(a, recent)
            a["score"] = self.scorer.score(a, a["echo"])

        bootstrap = not self.state.get("initialized")
        if not bootstrap:  # au premier lancement on n'appelle pas l'IA sur tout l'historique
            analysis = self.analyst.classify(fresh)
            for a in fresh:
                r = analysis.get(a["id"])
                if r:
                    bonus = 1 if a["echo"] >= 6 else 0
                    a["score"] = max(1, min(5, r["importance"] + bonus))
                    a["cats"] = [c for c in ("france", "energie", "monde") if c in r["categories"]] or a["cats"]
                    a["title_fr"] = r["titre_fr"]

        for a in fresh:
            a["sig"] = sorted(signature(title_of(a)))  # titre traduit : regroupe aussi les langues
            a["seen"] = now
            a["alerted"] = bootstrap
            known[a["id"]] = a

        if bootstrap:
            self.state["initialized"] = True
            self.state["last_digest_ts"] = now - 12 * 3600
            if datetime.now(self.tz).hour >= self.config["digest"]["hour"]:
                self.state["last_digest"] = datetime.now(self.tz).date().isoformat()
            log.info("premier lancement : %d articles mémorisés, pas d'alerte envoyée", len(fresh))
            self.tg.send("✅ <b>Bot d'actualités actif.</b>\nAlertes en direct pour les infos importantes, "
                         f"résumé chaque soir à {self.config['digest']['hour']} h (heure de Chine).",
                         topic=self.topics().get("urgent"))
        else:
            self.send_alerts(fresh, now)
            self.send_feed(fresh)

        self.maybe_digest(now)
        cutoff = now - 48 * 3600
        self.state["items"] = {k: v for k, v in known.items() if v["ts"] > cutoff}
        self.state["alerts"] = [t for t in self.state["alerts"] if now - t[0] < 24 * 3600]
        save_state(self.state)

    def send_alerts(self, fresh, now):
        cfg = self.config["alertes"]
        hour = datetime.now(self.tz).hour
        night = cfg["nuit"]["debut"] <= hour or hour < cfg["nuit"]["fin"]
        threshold = 5 if night else cfg["seuil_urgent"]
        sent_last_hour = sum(1 for t, _ in self.state["alerts"] if now - t < 3600)
        candidates = [a for a in fresh if a["score"] >= threshold and not a["digest_only"]
                      and now - a["ts"] < cfg["age_max_heures"] * 3600]
        for a in sorted(candidates, key=lambda x: (-x["score"], -x["poids"])):
            if sent_last_hour >= cfg["max_par_heure"] and a["score"] < 5:
                break
            sig = set(a["sig"])
            if any(similar(sig, set(s)) for _, s in self.state["alerts"]):
                continue  # histoire déjà signalée dans les dernières 24 h
            try:
                self.tg.send(alert_text(a), topic=self.topics().get("urgent"))
            except Exception as exc:
                log.error("envoi de l'alerte impossible : %s", exc)
                continue
            a["alerted"] = True
            self.state["alerts"].append([now, a["sig"]])
            sent_last_hour += 1

    def send_feed(self, fresh):
        """Fil discret par rubrique (notes >= seuil_fil), sans notification."""
        cfg = self.config["alertes"]
        topics = self.topics()
        if not topics and not cfg.get("fil_sans_onglets"):
            return
        for cat in ("france", "energie", "monde"):
            items = dedupe([a for a in fresh if cat in a["cats"] and a["score"] >= cfg["seuil_fil"]])[:15]
            if not items:
                continue
            head = "" if topics else f"<b>{CAT_LABEL[cat]}</b>\n\n"
            try:
                self.tg.send(head + "\n\n".join(line(i) for i in items), topic=topics.get(cat), silent=True)
            except Exception as exc:
                log.error("envoi du fil %s impossible : %s", cat, exc)

    def maybe_digest(self, now, force=False):
        local = datetime.now(self.tz)
        today = local.date().isoformat()
        if not force and (local.hour < self.config["digest"]["hour"] or self.state.get("last_digest") == today):
            return
        self.send_digest(now, local)
        self.state["last_digest"] = today
        self.state["last_digest_ts"] = now

    def send_digest(self, now, local):
        cfg = self.config["digest"]
        since = max(self.state.get("last_digest_ts") or 0, now - 30 * 3600)
        pool = [v for v in self.state["items"].values()
                if v.get("seen", v["ts"]) >= since and v["score"] >= cfg["score_min"]]
        sections = {}
        for cat in ("france", "energie", "monde"):
            items = dedupe([v for v in pool if cat in v["cats"]])
            top = items[:cfg["max_par_categorie"]]
            # Toujours un regard non occidental, même si les grands titres viennent d'Europe ou des États-Unis.
            elsewhere = [v for v in items[cfg["max_par_categorie"]:] if v["region"] not in WESTERN][:3]
            sections[cat] = (top, elsewhere)

        topics = self.topics()
        days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                  "septembre", "octobre", "novembre", "décembre"]
        date = f"{days[local.weekday()]} {local.day} {months[local.month - 1]}"
        intro = self.analyst.digest_intro({CAT_LABEL[c]: t for c, (t, _) in sections.items()})
        header = f"🌙 <b>Résumé du soir</b> · {date}\n{len(pool)} articles suivis depuis le dernier résumé"
        overview = header + (f"\n\n{esc(intro)}" if intro else "")

        blocks = []
        for cat, (top, elsewhere) in sections.items():
            if not top:
                continue
            text = f"<b>{CAT_LABEL[cat]}</b>\n\n" + "\n\n".join(line(i) for i in top)
            if elsewhere:
                text += "\n\n<b>Vu d'ailleurs</b>\n\n" + "\n\n".join(line(i) for i in elsewhere)
            blocks.append((cat, text))

        if topics:
            self.tg.send(overview, topic=topics.get("resume"))
            for cat, text in blocks:
                self.tg.send(text, topic=topics.get(cat), silent=True)
        else:
            self.tg.send(overview)
            for _, text in blocks:
                self.tg.send(text, silent=True)


# ---------------------------------------------------------------- commandes

def cmd_check(bot):
    from .feeds import fetch_source
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as pool:
        for source, items, err in pool.map(lambda s: fetch_source(s, 24 * 30), bot.sources):
            status = f"ERREUR {err}" if err else f"{len(items)} articles"
            print(f"{'✗' if err else '✓'} {source['name']:<40} {status}")


def cmd_chatid():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"] for u in data.get("result", []) if "message" in u}
    if not chats:
        print("Aucun message reçu : envoie d'abord un message à ton bot (ou dans le groupe), puis relance.")
    for cid, chat in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({chat.get('type')} : {chat.get('title') or chat.get('first_name')})")


def main():
    parser = argparse.ArgumentParser(description="Bot d'actualités Telegram")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "loop", "digest", "check", "chatid", "test"])
    parser.add_argument("--dry-run", action="store_true", help="affiche les messages sans les envoyer")
    parser.add_argument("--interval", type=int, default=300, help="secondes entre deux cycles (loop)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "chatid":
        return cmd_chatid()
    bot = Bot(dry_run=args.dry_run)
    if args.command == "check":
        return cmd_check(bot)
    if args.command == "test":
        return bot.tg.send("👋 Test réussi : ton bot d'actualités peut t'écrire.")
    if args.command == "digest":
        bot.maybe_digest(time.time(), force=True)
        return save_state(bot.state)
    if args.command == "loop":
        while True:
            try:
                bot.run()
            except Exception:
                log.exception("cycle en échec")
            time.sleep(args.interval)
    bot.run()


if __name__ == "__main__":
    main()
