# Amira — Avatar conférencier conversationnel

> Description technique du projet dans son état actuel (commit `9168dc2`).

---

## 1. Objectif

Amira (« Dr Amira Ben Salah ») est un **avatar vidéo parlant en français** qui :

1. **attend** qu'un animateur lui donne la parole dans une salle de conférence ;
2. **présente une conférence** d'environ 15 minutes sur l'intelligence artificielle dans l'enseignement supérieur et la recherche ;
3. **répond aux questions** du public, de façon brève, naturelle et sans inventer de faits.

Le public voit et entend Amira dans un navigateur, en général projeté sur grand écran. Un micro de salle transmet la voix de l'animateur et des personnes qui posent des questions.

---

## 2. Architecture générale

```
                         ┌───────────────────────────────┐
                         │         LiveKit Cloud         │
                         │  salle "ma-salle-test"        │
                         │  routage audio/vidéo (WebRTC) │
                         │  annuaire des workers         │
                         │  service de dispatch          │
                         └──────┬───────────┬────────┬───┘
                                │           │        │
               ┌────────────────┘           │        └──────────────────┐
               ▼                            ▼                           ▼
┌─────────────────────────┐   ┌──────────────────────────┐   ┌─────────────────────┐
│ Navigateur (spectateur) │   │ Worker local (agent.py)  │   │ Avatar bitHuman     │
│ frontend/index.html     │   │ logique d'Amira          │   │ (cloud)             │
│ micro → salle           │   │ VAD Silero (local)       │──▶│ reçoit l'audio TTS  │
│ affiche vidéo + son     │   │ STT/TTS Azure, LLM Groq  │   │ publie vidéo + son  │
└────────────▲────────────┘   └──────────────────────────┘   └─────────────────────┘
             │ GET / et /token
┌────────────┴────────────┐
│ token_server.py (local) │──▶ crée le jeton et, si besoin, le dispatch (API LiveKit)
└─────────────────────────┘
```

| Composant | Où il tourne | Rôle |
|---|---|---|
| **LiveKit Cloud** | Cloud (région EU West) | Héberge la salle, transporte l'audio et la vidéo entre participants, distribue les sessions aux workers. Ne contient **aucune logique** d'Amira. |
| **Worker `agent.py`** | Machine locale (WSL Ubuntu) | Le « cerveau » d'Amira : machine à états, détection du déclencheur, lecture du script, questions-réponses. |
| **Silero VAD** | Local | Détecte quand quelqu'un parle. |
| **Azure Speech** | Cloud (`francecentral`) | **STT** : voix vers texte (fr-FR). **TTS** : texte vers voix (`fr-FR-DeniseNeural`). |
| **Groq** | Cloud | LLM des questions-réponses (`openai/gpt-oss-120b` par défaut). |
| **bitHuman** | Cloud | Génère la vidéo de l'avatar, synchronisée sur l'audio, et la publie dans la salle. |
| **`token_server.py`** | Local | Sert l'interface, génère les jetons d'accès, déclenche le dispatch de l'agent. |
| **`frontend/index.html`** | Navigateur | Affiche l'avatar, joue le son, publie le micro. |

---

## 3. Arborescence

```
Avatar-Conversationnel/
├── README.md                     # Guide utilisateur (démarrage rapide en tête)
├── docs/
│   └── DESCRIPTION_PROJET.md     # Ce document
├── frontend/
│   ├── index.html                # Interface spectateur
│   └── logofac1.png, logoPoly.jpg
└── agent/
    ├── pyproject.toml, uv.lock   # Dépendances (gestionnaire uv)
    ├── .env.example              # Modèle de configuration
    ├── .env.local                # Configuration réelle et clés (non versionné)
    ├── tests/test_agent.py       # Gabarit de test (commenté, non utilisé)
    └── src/
        ├── agent.py                  # Agent Amira
        ├── token_server.py           # Serveur de l'interface et des jetons
        ├── conference_amira.md       # Script complet (~2 000 mots, 13 à 15 min)
        └── conference_amira_court.md # Script de test (~150 mots, ~1 min)
```

---

## 4. Cycle de vie complet

### 4.1 Démarrage

| Étape | Acteur | Ce qui se passe |
|---|---|---|
| 1 | `agent.py` (import) | Lecture de `.env.local`, chargement du script de conférence, compilation des règles du déclencheur, chargement du modèle Silero. |
| 2 | `agent.py` | `server = AgentServer()`, puis le décorateur `@server.rtc_session(agent_name=...)` enregistre la fonction `entrypoint` sous le nom `customerSupport`. |
| 3 | `cli.run_app(server)` | Crée le **worker**, qui se connecte à LiveKit Cloud et s'y inscrit (log `registered worker`). Il attend ensuite. |
| 4 | `token_server.py` | Sert l'interface sur `http://localhost:8080`. |

### 4.2 Arrivée d'Amira dans la salle

| Étape | Acteur | Ce qui se passe |
|---|---|---|
| 5 | Navigateur | Ouvre la page, qui appelle `GET /token`. |
| 6 | `token_server.py` | Vérifie la salle : si aucun agent n'est présent ni attendu, **crée le dispatch**. Renvoie l'URL et un jeton propre au spectateur. |
| 7 | LiveKit Cloud | Envoie le job au worker inscrit sous `customerSupport`. |
| 8 | Worker | Exécute `entrypoint(ctx)` dans un **processus enfant** dédié à cette session. |
| 9 | `entrypoint` | Construit la session (VAD, STT, LLM, TTS), démarre l'avatar bitHuman, place Amira en état ATTENTE, connecte la salle, enregistre le RPC de secours. |

**Règle importante : un dispatch donne une Amira.** Deux dispatchs dans la même salle donnent deux avatars. Le serveur de jetons empêche ce cas.

### 4.3 Machine à états d'Amira

```
 ┌───────────┐  "à vous la parole"  ┌────────────┐   fin du script   ┌───────────┐
 │  ATTENTE  │ ───────────────────► │ CONFÉRENCE │ ────────────────► │ QUESTIONS │
 └───────────┘   (ou RPC opérateur) └────────────┘                   └───────────┘
  AmiraWaiting                       AmiraWaiting                     AmiraQA
  LLM muet                           LLM muet, script lu              LLM actif
```

Principe : **le code décide quand Amira parle ; le LLM ne décide que du contenu de ses réponses aux questions.**

---

## 5. L'agent en détail (`agent/src/agent.py`)

### 5.1 Configuration

| Constante | Valeur par défaut | Rôle |
|---|---|---|
| `AGENT_NAME` | `customerSupport` | Nom sous lequel le worker s'inscrit. |
| `SCRIPT_PATH` | `conference_amira_court.md` | Script lu pendant la conférence (variable `CONFERENCE_SCRIPT`). |
| `TTS_RATE` | `0.9` | Débit de la voix (90 % du débit normal). |
| `WORDS_PER_MINUTE` | `135` | Sert à estimer la durée du script dans les logs. |
| `PAUSE_BETWEEN_PARAGRAPHS_S` | `1.0` | Silence entre deux paragraphes. |

### 5.2 Détection du déclencheur

La détection est **déterministe** : elle repose sur des règles et non sur le LLM, ce qui évite les faux départs comme les silences si le modèle ne suit pas la consigne.

| Fonction / élément | Rôle |
|---|---|
| `normalize()` | Met en minuscules, retire accents et ponctuation : « À vous la parole ! » devient « a vous la parole ». |
| `TRIGGER_PATTERNS` | Formulations acceptées : « à vous la parole », « la parole est à vous », « je vous cède la parole », « Amira, à vous », « on vous écoute »… |
| `_fuzzy_trigger()` | Tolère les erreurs de transcription (« avoue la parole ») par similarité ≥ 0,88 avec des phrases de référence. |
| `FUTURE_MARKERS` | Rejette les annonces au futur (« je lui donnerai la parole », « tout à l'heure »…). |
| `self._recent` | Recolle les fragments des 8 dernières secondes : « Amira… » puis « …à vous la parole ». |
| `GREETING_PATTERN` | « Bonjour Amira » donne une réponse fixe : « Je suis prête… ». |
| RPC `amira.start_conference` | Déclenchement de secours par un opérateur (non relié à l'interface pour l'instant). |

### 5.3 Script de conférence

`load_script()` découpe le fichier Markdown en paragraphes séparés par une ligne vide, et ignore les lignes qui commencent par `#`. Chaque paragraphe est lu par un appel `session.say()`. Les sigles sont écrits avec des points (`I.N.T.L.`, `U.V.T.`) pour que la voix les épelle.

Le script complet (`conference_amira.md`) suit ce plan : ouverture, accroche, impact sur l'enseignement, recherche, Tunisie, Polytech INTL et SigmaOS, messages au public, conclusion et invitation aux questions.

### 5.4 Les deux agents

**`AmiraWaiting`** gère l'ATTENTE et la CONFÉRENCE.
- `on_user_turn_completed()` analyse chaque phrase entendue, puis lève toujours `StopResponse` : le LLM ne répond jamais dans ces deux états.
- `start_conference()` est le point d'entrée unique, par la voix ou par le RPC. Il est idempotent : un second déclenchement est ignoré.
- `_run_conference()` lit les paragraphes l'un après l'autre. Ils ne sont pas interruptibles et n'entrent pas dans l'historique. Une erreur TTS fait sauter le paragraphe concerné sans arrêter la conférence. À la fin, `session.update_agent(AmiraQA())` fait passer Amira en mode questions.

**`AmiraQA`** gère les QUESTIONS.
- Ses instructions sont le prompt `QA_PROMPT`, et son historique repart à zéro.
- Les transcriptions vides ou d'un seul caractère sont ignorées.

### 5.5 Prompt des questions (`QA_PROMPT`)

- **Format oral** : pas de listes ni de markdown, nombres écrits comme on les prononce, **3 à 4 phrases courtes au maximum**.
- **Style** : chaleureux, accroches variées, analogies du quotidien, relance occasionnelle vers le public.
- **Base de connaissances limitée** : stratégie nationale de l'IA 2026-2030, feuille de route de l'U.V.T., modules d'éthique, projet HERACLES (Sfax), Smart Lab de Monastir, FORFIRE AI, Polytech INTL, SigmaOS, apports généraux de l'IA.
- **Prudence** : pour un fait absent de la base, Amira le dit et reste générale. Une question hors sujet reçoit une réponse avec humour, puis un retour au thème.

### 5.6 Pipeline vocal (`entrypoint`)

| Réglage | Valeur | Pourquoi |
|---|---|---|
| `turn_detection` | `"vad"` | La fin de tour est détectée par le silence. |
| `min_endpointing_delay` | `0.7 s` | 0,3 s coupait les questions au milieu des pauses naturelles. |
| `interruption` | désactivée (`ALLOW_INTERRUPTIONS=false`) | Dans une salle, la voix d'Amira revient dans le micro et l'interrompait elle-même. |
| `preemptive_generation` | désactivée | Inutile ici, et elle consommerait des tokens. |
| STT `phrase_list` | Amira, Polytech INTL, SigmaOS, U.V.T., FORFIRE… | Améliore la reconnaissance des noms propres. |
| TTS `sample_rate` | 24 kHz | Moins de données envoyées à l'avatar, donc moins de décalage entre les lèvres et la voix. |
| `audio_output` | `False` | L'audio est publié par l'avatar, pas par l'agent. |
| LLM de secours | OpenAI, si `OPENAI_API_KEY` et `OPENAI_FALLBACK_MODEL` sont définis | Prend le relais en cas de panne de Groq. Inactif par défaut. |

---

## 6. Serveur de jetons (`agent/src/token_server.py`)

Petit serveur `aiohttp` qui remplace `python3 -m http.server` ainsi que les commandes manuelles `lk token create` et `lk dispatch create`.

| Route | Rôle |
|---|---|
| `GET /` | Sert `frontend/index.html`. |
| `GET /<fichier>` | Sert les fichiers statiques (logo…). |
| `GET /token` | Renvoie `{url, token, agent}`. |

**Jeton** : une identité unique par connexion (`spectateur-xxxxxx`), un droit limité à la salle configurée. Les clés API ne quittent jamais le serveur.

**Dispatch sans doublon** (`ensure_agent`), protégé par un verrou :

| Situation dans la salle | Résultat (`agent`) |
|---|---|
| Un participant de type agent est présent | `already_present`, rien n'est fait |
| Un dispatch `customerSupport` est en attente | `already_pending`, rien n'est fait |
| Aucun des deux | `dispatched`, le dispatch est créé |
| Erreur d'API | `error` : le spectateur se connecte quand même |

---

## 7. Interface spectateur (`frontend/index.html`)

- Au chargement : `fetch("/token")`, puis `room.connect(url, token)`.
- **Vidéo** de l'avatar dans `<video muted autoplay playsinline>`. L'attribut `muted` évite le blocage de la lecture automatique par Chrome.
- **Son** dans un élément `<audio>` séparé, débloqué au premier clic.
- **Bouton micro** : active ou coupe le micro, avec annulation d'écho, réduction de bruit et contrôle de gain automatique.
- **Messages d'état** : « Connecté », « Avatar connecté ✅ », « Avatar déconnecté », erreurs.
- **Console (F12)** : statut du dispatch, participants présents, pistes reçues.

---

## 8. Configuration (`agent/.env.local`)

| Variable | Obligatoire | Utilisée par | Rôle |
|---|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | oui | agent, serveur de jetons | Accès au projet LiveKit Cloud |
| `AZURE_SPEECH_KEY` | oui | agent | STT et TTS |
| `AZURE_SPEECH_REGION` | non (`francecentral`) | agent | Région Azure |
| `AZURE_TTS_VOICE` | non (`fr-FR-DeniseNeural`) | agent | Voix d'Amira |
| `AZURE_TTS_RATE` | non (`0.9`) | agent | Débit de la voix |
| `AZURE_TTS_SAMPLE_RATE` | non (`24000`) | agent | Fréquence audio |
| `GROQ_API_KEY` | oui | agent | LLM |
| `GROQ_MODEL` | non (`openai/gpt-oss-120b`) | agent | Modèle LLM |
| `OPENAI_API_KEY`, `OPENAI_FALLBACK_MODEL` | non | agent | LLM de secours |
| `BITHUMAN_API_SECRET`, `BITHUMAN_AVATAR_ID` | oui | agent | Avatar |
| `CONFERENCE_SCRIPT` | non (`conference_amira_court.md`) | agent | Script à lire |
| `PARAGRAPH_PAUSE_S` | non (`1.0`) | agent | Pause entre paragraphes |
| `ALLOW_INTERRUPTIONS` | non (`false`) | agent | Autoriser l'interruption d'Amira |
| `AGENT_NAME` | non (`customerSupport`) | agent, serveur de jetons | Nom de dispatch (identique des deux côtés) |
| `ROOM_NAME` | non (`ma-salle-test`) | serveur de jetons | Salle |
| `AUTO_DISPATCH` | non (`true`) | serveur de jetons | `false` pour revenir au dispatch manuel |
| `FRONTEND_PORT` | non (`8080`) | serveur de jetons | Port de l'interface |

Pour la démo finale : `CONFERENCE_SCRIPT=conference_amira.md`.

---

## 9. Exploitation

### Lancement (depuis `agent/`, sous WSL)

```bash
lk room participants list ma-salle-test   # attendu : "room does not exist"
uv run python src/agent.py dev            # attendre "registered worker"
uv run python src/token_server.py         # interface sur le port 8080
# puis ouvrir http://localhost:8080 : cette ouverture déclenche l'arrivée d'Amira
```

### Arrêt propre

1. Fermer l'onglet du navigateur.
2. Ctrl+C **une seule fois** sur l'agent, puis attendre qu'il rende la main.
3. `lk room delete ma-salle-test`
4. Ctrl+C sur le serveur de jetons.

Après un arrêt brutal (plantage, mise en veille, terminal fermé), il faut vérifier la salle et la supprimer avant de relancer. Sinon, une « session fantôme » reste dans la salle et continue de consommer des crédits.

Le rechargement automatique du code n'existe plus : après chaque modification, il faut arrêter l'agent, supprimer la salle et relancer.

---

## 10. Consommation et coûts

| Service | Unité facturée | Remarque |
|---|---|---|
| LiveKit Cloud | Minutes de session d'agent | Plan Build : 1 000 min par mois offertes |
| bitHuman | Minutes d'avatar présent dans la salle | **Poste principal** : l'avatar consomme même quand il se tait |
| Azure STT | Secondes d'audio | Le micro est transcrit pendant toute la session, attente comprise |
| Azure TTS | Caractères | ~12 000 caractères par conférence complète |
| Groq | Tokens | Seulement pendant les questions. Modèle de raisonnement : des tokens internes s'ajoutent à chaque réponse |

---

## 11. Limites connues et pistes d'amélioration

| Limite | Conséquence | Piste |
|---|---|---|
| Les motifs « à vous Amira » et « on vous écoute » sont trop larges | « Bonjour à vous Amira » lance la conférence | Retirer ces motifs ou exiger un verbe de cession de parole |
| `FUTURE_MARKERS` est trop large (« après », « ensuite ») | « Après cette introduction, à vous la parole » ne déclenche pas | Ne tester ces marqueurs qu'à proximité du verbe |
| Pas de commande stop ni reset | Une conférence lancée par erreur dure jusqu'au bout | Ajouter un RPC `amira.stop` et un bouton opérateur |
| Le RPC de démarrage n'est relié à aucun bouton | Pas de secours si le STT rate le déclencheur | Ajouter un bouton opérateur dans l'interface |
| `AmiraQA` ne connaît pas le texte de la conférence | Les questions du type « revenez sur… » sont mal traitées, et la base de connaissances doit être tenue à jour à la main | Injecter le script dans `QA_PROMPT` |
| Longueur des réponses limitée seulement par le prompt | Réponses parfois trop longues | Limiter `max_completion_tokens`, et régler `reasoning_effort` sur une valeur faible |
| Le bloc `finally` de `_run_conference` bascule même en cas d'annulation | Erreur possible à la fermeture de la session | Ne basculer qu'en cas de fin normale |
| Le départ de l'avatar n'est pas détecté | Amira devient muette sans le signaler | Surveiller le départ du participant avatar et l'écrire dans les logs |
| Dispatch en attente alors que l'agent a planté | Le serveur de jetons n'en relance pas | `lk room delete`, puis rafraîchir la page |
| Aucun test automatisé | Régressions possibles sur le déclencheur | Tests pytest de `is_trigger` et `is_greeting_to_amira` |
| Version de `livekit-client` non figée (CDN) | Une nouvelle version majeure peut casser la page | Figer une version précise |
| `agent_name` dans le code et mode `dev` dépréciés | Avertissements, puis retrait dans une future version | Passer à `livekit.toml` et à `lk agent dev` |
| Sections 2, 5.6 et 6 du README obsolètes (Simli, token manuel) | Confusion pour un nouvel utilisateur | Les aligner sur ce document |
| Anciennes clés LiveKit présentes dans l'historique git | Risque si le dépôt devient public | Régénérer les clés |
