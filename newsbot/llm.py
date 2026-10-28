"""Analyse facultative par Claude : note, catégories, titre en français, résumé du soir.

Activée seulement si ANTHROPIC_API_KEY est défini. En cas d'erreur, le bot
retombe sur la notation par mots-clés.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

PROFILE = (
    "Le lecteur est un étudiant français en master d'ingénierie nucléaire qui vit en Chine. "
    "Il suit trois rubriques : France (politique, société, grands événements), "
    "Énergie (nucléaire, pétrole, gaz, charbon, hydro, solaire, éolien, grands projets mondiaux, "
    "géopolitique de l'énergie, découvertes scientifiques) et Monde (géopolitique globale)."
)

SCALE = (
    "Note d'importance de 1 à 5 :\n"
    "5 = événement historique ou choc immédiat (guerre déclarée, attentat majeur, chute d'un gouvernement, "
    "accident nucléaire, catastrophe naturelle meurtrière de grande ampleur, choc pétrolier).\n"
    "4 = info majeure du jour à connaître tout de suite (décision politique forte, escalade militaire, "
    "annonce énergétique de premier plan, élection décisive).\n"
    "3 = info notable qui mérite le résumé du soir en bonne place.\n"
    "2 = info secondaire.\n"
    "1 = anecdotique, publicité, sport, people, horoscope.\n"
    "Sois exigeant : la plupart des articles valent 1 à 3. Un article scientifique dépasse rarement 3."
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "importance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["france", "energie", "monde"]},
                    },
                    "titre_fr": {"type": "string"},
                },
                "required": ["id", "importance", "categories", "titre_fr"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class Analyst:
    def __init__(self, config):
        self.enabled = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.model = config.get("llm", {}).get("model", "claude-opus-5-5")
        self.effort = config.get("llm", {}).get("effort", "low")
        if self.enabled:
            import anthropic
            self.client = anthropic.Anthropic()

    def _call(self, system, prompt, max_tokens, schema=None):
        output_config = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )
        # Les modèles Opus / Sonnet / Fable acceptent un repli automatique si une requête est refusée.
        if not self.model.startswith("claude-haiku"):
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            response = self.client.messages.create(**kwargs)
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"réponse incomplète ({response.stop_reason})")
        return next(b.text for b in response.content if b.type == "text")

    def classify(self, items, batch=40):
        """Renvoie {id: {importance, categories, titre_fr}} pour les articles analysés."""
        if not self.enabled or not items:
            return {}
        results = {}
        system = f"{PROFILE}\n\n{SCALE}\n\nPour chaque article : note, rubriques (une ou plusieurs), et titre traduit en français clair (garde-le tel quel s'il est déjà en français)."
        for start in range(0, len(items), batch):
            chunk = items[start:start + batch]
            lines = [
                {"id": it["id"], "source": it["source"], "region": it["region"],
                 "titre": it["title"], "extrait": it["summary"][:250]}
                for it in chunk
            ]
            try:
                text = self._call(system, json.dumps(lines, ensure_ascii=False), 16000, CLASSIFY_SCHEMA)
                for row in json.loads(text)["items"]:
                    results[row["id"]] = row
            except Exception as exc:
                log.warning("analyse IA impossible pour %d articles : %s", len(chunk), exc)
        return results

    def digest_intro(self, sections):
        """Court texte de synthèse en français, ou None si l'IA n'est pas disponible."""
        if not self.enabled:
            return None
        lines = []
        for cat, items in sections.items():
            lines.append(f"## {cat}")
            lines += [f"- [{it['score']}/5] {it.get('title_fr') or it['title']} ({it['source']})" for it in items]
        system = (
            f"{PROFILE}\nRédige le résumé du soir en français, à partir des titres fournis : "
            "pour chaque rubrique, 3 à 5 phrases qui racontent l'essentiel de la journée et les liens entre les faits. "
            "Pas d'invention au-delà des titres. Texte brut, avec un titre de rubrique précédé d'un emoji "
            "(🇫🇷 France, ⚡ Énergie, 🌍 Monde). 250 mots maximum au total."
        )
        try:
            return self._call(system, "\n".join(lines), 4000)
        except Exception as exc:
            log.warning("synthèse IA impossible : %s", exc)
            return None
