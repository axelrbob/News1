# Bot d'actualités Telegram

Un bot qui surveille environ 95 sources dans le monde entier et t'envoie sur Telegram :

- **une alerte immédiate** quand une info importante tombe (note 4 ou 5 sur 5) ;
- **un résumé chaque soir à 21 h** (heure de Chine), classé par rubrique : 🇫🇷 France, ⚡ Énergie, 🌍 Monde ;
- en option, **des onglets** : un groupe Telegram avec un sujet par rubrique, plus un sujet « Urgent » et un sujet « Résumé du soir ».

## Comment ça marche

1. **Collecte** : toutes les 10 minutes, le bot lit les flux RSS listés dans `sources.yaml` : presse française, grands médias internationaux, et beaucoup de sources non occidentales (SCMP, Caixin, CGTN, Nikkei, NHK, Yonhap, CNA, The Hindu, Dawn, Al Jazeera, Middle East Eye, Tehran Times, L'Orient-Le Jour, Jeune Afrique, Africanews, Meduza, TASS, MercoPress, El País América, Folha…), la presse énergie (World Nuclear News, AIEA, ANS, NucNet, SFEN, OilPrice, Rigzone, Carbon Brief, pv magazine, Hydro Review, Dialogue Earth…) et des revues scientifiques (Nature, Nature Energy, Science, Physics World, Annals of Nuclear Energy, Progress in Nuclear Energy, Nuclear Engineering and Design, arXiv nucl-ex, CERN, ITER).
2. **Classement** : chaque article reçoit une ou plusieurs rubriques (une info peut être dans plusieurs cases).
3. **Note d'importance de 1 à 5** :
   - sans IA : mots-clés (« breaking », « attentat », « motion de censure », « accident nucléaire »…), poids du média, tes centres d'intérêt, et surtout **le nombre de médias différents qui reprennent la même histoire** ;
   - avec IA (facultatif, voir plus bas) : Claude lit les titres, note chaque article, corrige les rubriques et **traduit les titres en français**.
4. **Envoi** :
   - note 4 ou 5 : alerte immédiate (la nuit, de 23 h à 7 h, seules les notes 5 te réveillent ; maximum 6 alertes par heure ; une même histoire n'est signalée qu'une fois) ;
   - note 3 : publiée en silence dans l'onglet de sa rubrique (mode onglets uniquement) ;
   - tout le reste à partir de 2 : résumé du soir, avec une rubrique « Vu d'ailleurs » qui garantit des sources hors Europe et États-Unis.
   - Les revues scientifiques ne déclenchent jamais d'alerte : elles vont dans le résumé du soir.

Tout se règle dans `config.yaml` (heure du résumé, seuils, mots-clés) et `sources.yaml` (ajouter ou retirer un média).

## Installation (environ 15 minutes)

### 1. Créer le bot Telegram

1. Dans Telegram, ouvre **@BotFather**, envoie `/newbot`, choisis un nom. Il te donne un **token** du type `123456:ABC-DEF...`. Garde-le secret.
2. **Conversation privée** (le plus simple) : envoie un message quelconque à ton bot.
   **Ou mode onglets** (recommandé) : crée un groupe, active **Sujets** (Topics) dans les paramètres du groupe, ajoute le bot comme **administrateur** avec le droit « Gérer les sujets », puis écris un message dans le groupe. Le bot créera lui-même les onglets 🔴 Urgent, 🇫🇷 France, ⚡ Énergie, 🌍 Monde et 🌙 Résumé du soir. Tu peux ensuite couper les notifications des onglets France, Énergie et Monde et ne garder que Urgent et Résumé.

### 2. Mettre le code sur GitHub

1. Crée un dépôt **public** sur GitHub (les Actions sont gratuites et illimitées sur un dépôt public ; ton token reste privé dans les Secrets). Sur un dépôt privé, le quota gratuit est de 2 000 minutes par mois : passe alors le `cron` du fichier `.github/workflows/newsbot.yml` à `*/30`.
2. Envoie-y tous les fichiers de ce dossier.
3. Dans **Settings → Secrets and variables → Actions**, ajoute :
   - `TELEGRAM_BOT_TOKEN` : le token de BotFather ;
   - `TELEGRAM_CHAT_ID` : voir l'étape suivante ;
   - `ANTHROPIC_API_KEY` : facultatif, pour l'analyse par IA.

### 3. Trouver ton TELEGRAM_CHAT_ID

Dans l'onglet **Actions** du dépôt, lance le workflow **newsbot** à la main (*Run workflow*) avec la commande `chatid`. Le journal affiche une ligne `TELEGRAM_CHAT_ID=...` : copie ce nombre dans le secret. (Pour un groupe, il commence par `-100`.)

### 4. Tester puis laisser tourner

- Lance le workflow avec `test` : tu dois recevoir « Test réussi ».
- Lance-le avec `check` : le journal liste chaque source avec ✓ ou ✗. Les sources en ✗ peuvent être retirées de `sources.yaml` (un flux en panne ne bloque jamais les autres).
- Ensuite, il tourne tout seul toutes les 10 minutes. Le premier passage mémorise les articles existants sans rien envoyer, pour ne pas te noyer.

## Analyse par IA (facultatif)

Sans clé, le bot fonctionne avec les mots-clés : c'est gratuit mais moins fin, et les titres restent dans leur langue d'origine (anglais, espagnol, portugais…).

Avec une clé API Anthropic (console.anthropic.com), Claude note chaque article, traduit les titres en français et écrit un vrai texte de synthèse en haut du résumé du soir. Le modèle se choisit dans `config.yaml` → `llm.model`. Ordre de grandeur pour environ 3 000 articles par jour :

| Modèle | Coût estimé |
|---|---|
| `claude-haiku-5-5` | environ 4 $ par mois |
| `claude-opus-5-5` (réglage par défaut) | environ 150 $ par mois |

Pour ce travail de tri, `claude-haiku-5-5` suffit largement.

## Lancer ailleurs que sur GitHub

GitHub Actions peut retarder une exécution de quelques minutes, donc une alerte arrive en général 5 à 20 minutes après publication. Pour du quasi temps réel, n'importe quelle machine allumée en permanence (petit serveur, Raspberry Pi, VM gratuite Oracle Cloud…) fait l'affaire :

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... ANTHROPIC_API_KEY=...   # la clé est facultative
python -m newsbot loop --interval 180
```

Autres commandes : `python -m newsbot digest` (résumé immédiat), `check`, `test`, `chatid`, et `--dry-run` pour afficher les messages au lieu de les envoyer.

## Bon à savoir

- **Depuis la Chine**, Telegram demande un VPN sur ton téléphone pour recevoir les messages. Le bot, lui, tourne sur les serveurs de GitHub et n'est pas concerné. WhatsApp aurait le même problème, et son API officielle passe par un compte Meta Business, d'où le choix de Telegram.
- GitHub met en pause les tâches planifiées d'un dépôt public après 60 jours sans aucun commit. Si les messages s'arrêtent, va dans l'onglet Actions et réactive le workflow.
- La mémoire du bot (articles déjà vus) est gardée dans le cache de GitHub Actions. Si elle est perdue, le bot redémarre proprement sans renvoyer les anciennes infos.
