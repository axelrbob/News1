"""Classement par catégorie et note d'importance (1 à 5) par mots-clés."""
import re
import unicodedata

CATEGORIES = ("france", "energie", "monde")
STOP = set("""
the a an of to in on for and with from at by as is are was were be after over amid says said new
les des une un le la du de et en au aux pour par sur dans avec qui que est sont apres plus selon
""".split())


def normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def compile_keywords(words):
    """Mot court (<= 4 lettres) : mot exact (pluriel accepté). Mot long : suffixe libre de 4 lettres."""
    parts = []
    for w in words:
        w = re.escape(normalize(str(w)))
        parts.append(rf"\b{w}s?\b" if len(w) <= 4 else rf"\b{w}\w{{0,4}}\b")
    return re.compile("|".join(parts)) if parts else None


class Scorer:
    def __init__(self, config):
        self.cat_re = {c: compile_keywords(config["categories"].get(c, [])) for c in CATEGORIES}
        imp = config["importance"]
        self.flash = compile_keywords(imp.get("flash", []))
        self.major = compile_keywords(imp.get("majeur", []))
        self.interest = compile_keywords(imp.get("interet", []))

    def categorize(self, item):
        text = normalize(f"{item['title']} {item['summary']}")
        cats = {item["default_cat"]}
        for cat, rx in self.cat_re.items():
            if rx and rx.search(text):
                cats.add(cat)
        # Une info française dans un média étranger reste dans "France" ; "monde" sert de fourre-tout.
        return [c for c in CATEGORIES if c in cats]

    def score(self, item, corroboration):
        title = normalize(item["title"])
        text = normalize(f"{item['title']} {item['summary']}")
        s = 1.0
        if item["poids"] >= 3:
            s += 0.5
        if self.flash and self.flash.search(title):
            s += 2
        if self.major:
            s += min(len(set(self.major.findall(text))), 2) * 1.5
        if self.interest and self.interest.search(text):
            s += 1
        # La même histoire reprise par plusieurs médias différents = signal fort.
        if corroboration >= 6:
            s += 2
        elif corroboration >= 3:
            s += 1
        if item["science"]:
            s = min(s, 3)
        return max(1, min(5, int(s + 0.5)))


def signature(title):
    words = re.findall(r"[a-z0-9]{3,}", normalize(title))
    return {w for w in words if w not in STOP}


def similar(sig_a, sig_b):
    if not sig_a or not sig_b:
        return False
    common = len(sig_a & sig_b)
    return common >= 3 and common / len(sig_a | sig_b) >= 0.3


def corroboration(item, recent):
    """Nombre de médias distincts qui ont publié un titre proche."""
    sig = signature(item["title"])
    sources = {item["source"].split(" - ")[0]}
    for other in recent:
        if similar(sig, other["sig"]):
            sources.add(other["source"].split(" - ")[0])
    return len(sources)
