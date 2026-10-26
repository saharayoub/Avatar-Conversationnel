# 🎓 Avatar Conversationnel Académique 3D

> Un assistant virtuel en français, capable de donner une conférence de 15 minutes et de répondre aux questions sur l'IA dans l'enseignement supérieur et la recherche.

**Projet Ingénieur — Diplôme National d'Ingénieur Big Data & Intelligence Artificielle**  
**Polytech INTL Tunis — Sahar Ayoub — 2025/2026**

---

## 📑 Table des matières

- [1. Présentation](#1-présentation)
- [2. Architecture](#2-architecture)
- [3. Prérequis](#3-prérequis)
- [4. Installation](#4-installation)
- [5. Configuration des services cloud](#5-configuration-des-services-cloud)
- [6. Lancement du projet](#6-lancement-du-projet)
- [7. Scénario d'utilisation détaillé](#7-scénario-dutilisation-détaillé)
- [8. Structure du projet](#8-structure-du-projet)
- [9. Dépannage](#9-dépannage)
- [10. Contact](#10-contact)

---

## 1. Présentation

Cet avatar conversationnel combine **5 technologies cloud** pour créer une expérience interactive en temps réel :

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| 🎤 **Reconnaissance vocale** | Azure Speech | Transforme la voix de l'utilisateur en texte (français) |
| 🧠 **Intelligence conversationnelle** | Groq LLM | Génère les réponses (`openai/gpt-oss-20b`) |
| 🔊 **Synthèse vocale** | Azure Speech | Génère la voix (`fr-FR-VivienneMultilingualNeural`) |
| 👤 **Avatar vidéo** | Simli | Génère la vidéo avec synchronisation labiale |
| 🌐 **Orchestration** | LiveKit Cloud | Transport WebRTC temps réel |

L'avatar incarne **Dr. Amira Ben Salah**, une universitaire tunisienne spécialisée dans l'IA appliquée à l'enseignement supérieur.

### ✨ Fonctionnalités principales

- ✅ **Activation vocale** : l'avatar démarre sur commande (« Amira, commence »)
- ✅ **Conférence structurée** : introduction, impact de l'IA, cas de la Tunisie
- ✅ **Mode Q&A** : répond aux questions des invités (2-3 phrases par réponse)
- ✅ **Bouton micro** : permet de couper/réactiver le micro à tout moment
- ✅ **Refus hors-sujet** : l'avatar reste dans son domaine de spécialité

---

## 2. Architecture

```
┌────────────┐   ┌──────────┐   ┌────────┐   ┌──────────┐   ┌──────────┐
│ Utilisateur│──▶│ STT Azure│──▶│LLM Groq│──▶│ TTS Azure│──▶│  Simli   │
│  (Micro)   │   │Voix→Texte│   │Cerveau │   │Texte→Voix│   │  Avatar  │
└────────────┘   └──────────┘   └────────┘   └──────────┘   └──────────┘
      │               │              │              │              │
      └───────────────┴──────────────┴──────────────┴──────────────┘
                              │
                  ┌───────────▼────────────┐
                  │     LiveKit Cloud      │
                  │  Orchestration WebRTC  │
                  └───────────┬────────────┘
                              │
                  ┌───────────▼────────────┐
                  │      Frontend Web      │
                  │      (Navigateur)      │
                  └────────────────────────┘
```

**Flux de données :**
1. L'utilisateur parle dans son micro
2. Azure STT transcrit la voix en texte
3. Groq LLM génère une réponse appropriée
4. Azure TTS transforme le texte en voix
5. Simli génère la vidéo avec synchronisation labiale
6. LiveKit orchestre l'ensemble et transmet au navigateur

---

## 3. Prérequis

### 🖥️ Système d'exploitation
- **Windows 10/11** avec **WSL2** + Ubuntu 22.04+
- Ou Linux / macOS directement

### 🛠️ Outils à installer

| Outil | Version | Utilité |
|-------|---------|---------|
| **WSL2 + Ubuntu** | 22.04+ | Environnement Linux sur Windows |
| **Python** | 3.12 | Langage de l'agent |
| **uv** | Dernière | Gestionnaire de paquets Python |
| **LiveKit CLI** | 2.18.8+ | Gestion de l'agent |
| **Git** | Dernière | Versionnement |
| **Navigateur web** | Chrome / Edge / Firefox | Interface utilisateur |

### ✅ Vérification des prérequis

Dans votre terminal Ubuntu, lancez :

```bash
python3 --version    # Doit afficher Python 3.12.x
uv --version         # Doit afficher la version de uv
lk --version         # Doit afficher la version de LiveKit CLI
git --version        # Doit afficher la version de git
```

Si une commande échoue → voir la section [Installation](#4-installation).

---

## 4. Installation

### Étape 1 — Installer WSL2 et Ubuntu (Windows uniquement)

Dans **PowerShell** en administrateur :

```powershell
wsl --install -d Ubuntu
```

Redémarrez votre PC. Ubuntu s'ouvrira automatiquement à la première ouverture.

### Étape 2 — Installer Python 3.12

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip
```

### Étape 3 — Installer uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Fermez puis rouvrez le terminal, puis vérifiez :

```bash
uv --version
```

### Étape 4 — Installer LiveKit CLI

```bash
curl -sSL https://get.livekit.io/cli | bash
```

Vérifiez :

```bash
lk --version
```

### Étape 5 — Cloner le projet

```bash
cd ~
git clone https://github.com/saharayoub/Avatar-Conversationnel.git
cd Avatar-Conversationnel
```

### Étape 6 — Installer les dépendances Python

```bash
cd agent
uv sync
```

Cela installe automatiquement :
- `livekit-agents` (framework principal)
- `livekit-plugins-azure` (STT + TTS)
- `livekit-plugins-groq` (LLM)
- `livekit-plugins-silero` (VAD)
- `livekit-plugins-simli` (avatar vidéo)

---

## 5. Configuration des services cloud

⚠️ **Vous devez créer vos propres comptes** sur les 4 services ci-dessous.
**Durée estimée : ~30 minutes.** Tous les plans utilisés sont **gratuits**.

### 🔵 1. LiveKit Cloud (gratuit)

1. Rendez-vous sur [cloud.livekit.io](https://cloud.livekit.io)
2. Créez un compte gratuit
3. Créez un projet nommé `avatar-test`
4. Notez dans **Settings → Keys** :
   - `LIVEKIT_URL` (ex: `wss://xxxxx.livekit.cloud`)
   - `LIVEKIT_API_KEY`
   - `LIVEKIT_API_SECRET`

### 🟦 2. Azure Speech (gratuit avec Azure for Students)

1. Rendez-vous sur [portal.azure.com](https://portal.azure.com)
2. Créez un compte **Azure for Students** (gratuit avec email universitaire)
3. Créez une ressource **Speech** avec :
   - **Région** : `francecentral`
   - **Pricing tier** : `Free F0`
4. Notez dans **Keys and Endpoint** :
   - `AZURE_SPEECH_KEY` (Clé 1)
   - `AZURE_SPEECH_REGION` = `francecentral`

### 🟪 3. Groq (gratuit, LLM ultra-rapide)

1. Rendez-vous sur [console.groq.com](https://console.groq.com)
2. Créez un compte gratuit
3. **API Keys** → **Create API Key**
4. Copiez : `GROQ_API_KEY` (commence par `gsk_`)

### 🟢 4. Simli (gratuit, 200 minutes offertes)

1. Rendez-vous sur [app.simli.com](https://app.simli.com)
2. Créez un compte gratuit
3. **API keys** → copiez : `SIMLI_API_KEY`
4. **Create Avatar** → choisissez un preset (ex : "Tina")
5. Notez son **Face ID** : `SIMLI_FACE_ID`

### 📝 5. Créer le fichier `.env.local`

Dans le dossier `agent/`, copiez le modèle :

```bash
cd ~/Avatar-Conversationnel/agent
cp .env.example .env.local
nano .env.local
```

Remplissez avec vos propres clés :

```bash
LIVEKIT_URL=wss://votre-projet.livekit.cloud
LIVEKIT_API_KEY=votre_cle_api
LIVEKIT_API_SECRET=votre_secret
AZURE_SPEECH_KEY=votre_cle_azure
AZURE_SPEECH_REGION=francecentral
GROQ_API_KEY=gsk_votre_cle
SIMLI_API_KEY=votre_cle_simli
SIMLI_FACE_ID=votre_face_id
```

Sauvegardez : `Ctrl+O`, `Entrée`, `Ctrl+X`.

⚠️ **Ne committez JAMAIS ce fichier** — il est déjà dans `.gitignore`.

### 🔑 6. Générer un token LiveKit

Pour que le navigateur puisse se connecter, il faut un token :

```bash
lk token create \
  --api-key VOTRE_API_KEY \
  --api-secret VOTRE_SECRET \
  --join --room ma-salle-test \
  --identity spectateur \
  --url wss://votre-projet.livekit.cloud \
  --valid-for 24h
```

Copiez la longue chaîne `eyJ...` qui s'affiche.

Collez-la dans `frontend/index.html` à la ligne :

```javascript
const LIVEKIT_TOKEN = "eyJ...votre_token...";
```

---

## 6. Lancement du projet

Le projet nécessite **3 terminaux ouverts en parallèle**.

### 📟 Terminal 1 — Agent Python

```bash
cd ~/Avatar-Conversationnel/agent
uv run python src/agent.py dev
```

⏳ **Attendez de voir** :
```
INFO livekit.agents - registered worker {"agent_name": "customerSupport", ...}
```

Laissez ce terminal ouvert.

### 📟 Terminal 2 — Dispatch vers la salle

Ouvrez un **nouveau terminal** :

```bash
lk dispatch create --agent-name customerSupport --room ma-salle-test
```

⏳ **Attendez de voir** :
```
Dispatch created: id:"AD_..." agent_name:"customerSupport" room:"ma-salle-test"
```

### 📟 Terminal 3 — Serveur web

Ouvrez un **3ᵉ terminal** :

```bash
cd ~/Avatar-Conversationnel/frontend
python3 -m http.server 8000
```

### 🌐 Navigateur

Ouvrez : **http://localhost:8000**

---

## 7. Scénario d'utilisation détaillé

Voici le **scénario complet** d'une session avec l'avatar, étape par étape.

---

### 📍 Étape 1 — Ouverture de la page

**Action utilisateur** : Ouvrir `http://localhost:8000`.

**Affichage** :
- En-tête avec logo Polytech INTL
- Titre « Assistant IA — Enseignement Supérieur et Recherche »
- Zone vidéo vide au centre
- Bouton **gris** en bas : **🔇 Micro coupé**

**Arrière-plan** :
- Le navigateur se connecte à LiveKit Cloud
- L'agent Python rejoint la salle `ma-salle-test`
- L'avatar Simli rejoint la salle (mais reste invisible)

---

### 📍 Étape 2 — Activation du micro

**Action utilisateur** : Cliquer sur le bouton **🔇 Micro coupé**.

**Affichage** :
- Le bouton devient **vert** : **🔊 Micro actif**
- L'avatar apparaît dans la zone vidéo après **5 à 10 secondes**
- L'avatar dit : **« Je suis prête, cédez-moi la parole quand vous voulez. »**

**Arrière-plan** :
- Le microphone de l'utilisateur est activé
- L'audio du navigateur est débloqué
- Simli reçoit le premier audio et génère la vidéo

---

### 📍 Étape 3 — Activation de la conférence

**Action utilisateur** : Dire clairement à voix haute :

> **« Amira, commence »**

**Affichage** : L'avatar démarre sa conférence.

**Arrière-plan** :
1. **Azure STT** transcrit « Amira, commence » en texte
2. **Groq LLM** détecte l'instruction et appelle l'outil `start_conference`
3. Le LLM génère la conférence (5 parties)
4. **Azure TTS** transforme le texte en voix française
5. **Simli** reçoit l'audio et génère la vidéo avec synchronisation labiale
6. La vidéo est publiée dans la salle LiveKit
7. Le navigateur reçoit et affiche la vidéo

**Variantes acceptées** :
- « Je cède la parole à Amira »
- « Amira, tu peux commencer »
- « Amira, vas-y »

---

### 📍 Étape 4 — Écoute de la conférence

**Action utilisateur** : **Ne pas parler**. Écouter l'avatar.

**Structure de la conférence** :

| Partie | Contenu | Durée |
|--------|---------|-------|
| 1 | Introduction personnelle | 30 s |
| 2 | Impact général de l'IA dans l'enseignement | 3 min |
| 3 | Impact sur la recherche scientifique | 2 min |
| 4 | Cas de la Tunisie (exemples concrets) | 5 min |
| 5 | Conclusion + invitation aux questions | 1 min |

**Durées selon le mode** :
- `test` : ~2 minutes (pour tests rapides)
- `production` : ~15 minutes (démo finale)

---

### 📍 Étape 5 — Transition vers les questions

À la fin de la conférence, l'avatar dit :

> « Je vous remercie pour votre attention. **J'attends maintenant vos questions.** »

---

### 📍 Étape 6 — Phase Q&A

**Action utilisateur** : Poser une question à voix haute.

**Exemples de questions** :
- « Comment l'IA aide-t-elle les étudiants tunisiens ? »
- « Quels sont les défis de l'IA en Tunisie ? »
- « L'IA va-t-elle remplacer les enseignants ? »

**Comportement de l'avatar** :
- Répond en **2 à 3 phrases maximum**
- Ton académique mais accessible
- Explique les termes techniques

---

### 📍 Étape 7 — Cas particuliers

| Vous dites | Réponse de l'avatar |
|-----------|---------------------|
| « Bonjour » | « Bonjour. » |
| « Merci » | « Je vous en prie. » |
| « Au revoir » | « Au revoir. » |
| « Quelle est la capitale de la France ? » | « Je n'ai pas les informations nécessaires pour répondre à cette question. » |

---

### 📍 Étape 8 — Désactivation du micro

**Action utilisateur** : Cliquer sur **🔊 Micro actif**.

**Résultat** :
- Le bouton redevient **gris** : **🔇 Micro coupé**
- L'avatar n'entend plus rien

**Utilité** : parler à voix basse à quelqu'un d'autre sans que l'avatar ne capte la conversation.

**Pour réactiver** : re-cliquer sur le bouton → il redevient vert.

---

### 📍 Étape 9 — Arrêt propre

⚠️ **Ordre critique** pour économiser les crédits Simli :

1. **Fermer l'onglet du navigateur EN PREMIER** 🚪
2. Attendre **5 secondes** ⏳
3. `Ctrl+C` dans le **Terminal 1** (agent)
4. `Ctrl+C` dans le **Terminal 3** (serveur web)

**Pourquoi cet ordre ?** Fermer le navigateur en premier force Simli à libérer immédiatement la session cloud, ce qui arrête la consommation de crédits.

---

## 8. Structure du projet

```
Avatar-Conversationnel/
│
├── README.md                       # Ce fichier
├── .gitignore                      # Fichiers exclus de Git
│
├── agent/                          # Backend Python
│   ├── src/
│   │   └── agent.py                # Code principal de l'agent
│   ├── pyproject.toml              # Dépendances Python
│   ├── uv.lock                     # Lock file
│   ├── .env.example                # Modèle de configuration
│   └── .env.local                  # Config réelle (NON committé)
│
└── frontend/                       # Interface web
    ├── index.html                  # Page web
    └── logo.png                    # Logo Polytech INTL
```

---

## 9. Dépannage

### ❌ L'avatar n'apparaît pas après avoir cliqué sur le bouton

| Cause possible | Solution |
|----------------|----------|
| Dispatch non fait | Relancer `lk dispatch create --agent-name customerSupport --room ma-salle-test` |
| Token LiveKit expiré | Régénérer un token (voir section 5.6) |
| Session fantôme | Attendre 2 minutes, puis relancer |
| Crédits Simli épuisés | Vérifier sur [app.simli.com](https://app.simli.com) |

### ❌ L'agent ne démarre pas la conférence

- Vérifier que le micro est bien **actif** (bouton vert)
- Dire clairement : **« Amira, commence »**
- Essayez les variantes : « Je cède la parole à Amira », « Amira, vas-y »

### ❌ Erreur Python au lancement

Vérifiez la syntaxe du fichier `agent.py` :

```bash
cd ~/Avatar-Conversationnel/agent
python3 -c "import ast; ast.parse(open('src/agent.py').read()); print('✅ OK')"
```

### ❌ `ModuleNotFoundError`

Réinstallez les dépendances :

```bash
cd ~/Avatar-Conversationnel/agent
uv sync
```

### ❌ Erreur de connexion LiveKit

Vérifiez votre fichier `.env.local` :

```bash
cat ~/Avatar-Conversationnel/agent/.env.local
```

Assurez-vous que `LIVEKIT_URL`, `LIVEKIT_API_KEY` et `LIVEKIT_API_SECRET` sont corrects.

### ❌ `Command 'python' not found`

Utilisez `python3` au lieu de `python` :

```bash
python3 --version
```

---

## 10. Contact

**Sahar Ayoub**  
📧 Email : sahar.ayoub156@gmail.com  
🐙 GitHub : [github.com/saharayoub](https://github.com/saharayoub)

---

### Liste des commandes

uv run python src/agent.py dev

lk token create --api-key APIddsLrJWEkYP5 --api-secret gVCm6t6wACEPa71zJHlZIqyLx30uxQtFXXgA17cDVlB --join --room ma-salle-test --identity spectateur --url wss://test-vjsyazm0.livekit.cloud --valid-for 24h^C

lk dispatch create --agent-name customerSupport --room ma-salle-test --api-key APIddsLrJWEkYP5 --api-secret gVCm6t6wACEPa71zJHlZIqyLx30uxQtFXXgA17cDVlB --url wss://test-vjsyazm0.livekit.cloud

python3 -m http.server 8000

** **
