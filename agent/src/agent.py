"""
Amira - conférencière virtuelle (LiveKit Agents + bitHuman).

Machine à états simple et déterministe :

    ATTENTE  --(déclencheur "à vous la parole")-->  CONFÉRENCE  --(fin du script)-->  QUESTIONS
              (silence, ignore tout le reste)        (script lu, non interruptible)    (LLM, interactif)

- Le déclencheur est détecté par le CODE (expressions régulières sur la transcription),
  pas par le LLM : pas de faux départ, pas de silence si le LLM ne suit pas la consigne.
- La conférence est un SCRIPT écrit à l'avance (conference_amira.md), lu par le TTS
  paragraphe par paragraphe : durée maîtrisée, aucun risque d'invention ni de coupure LLM.
- Le LLM ne sert qu'à la session de questions-réponses.
- Un opérateur peut aussi lancer la conférence par RPC ("amira.start_conference") en secours.
"""

import asyncio
import difflib
import logging
import time
from collections import deque
import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    StopResponse,
    TurnHandlingOptions,
    cli,
    room_io,
)
from livekit.agents import llm as lk_llm
from livekit.plugins import azure, bithuman, groq, openai, silero
from livekit.plugins.azure.tts import ProsodyConfig

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent
# Chemin absolu : fonctionne quel que soit le dossier depuis lequel on lance l'agent.
load_dotenv(SRC_DIR.parent / ".env.local")

logger = logging.getLogger("amira")

AGENT_NAME = os.getenv("AGENT_NAME", "customerSupport")  # à terme : livekit.toml ([agent] name)
# Choix du script via .env.local : CONFERENCE_SCRIPT=conference_amira_court.md pour un test rapide.
SCRIPT_PATH = SRC_DIR / os.getenv("CONFERENCE_SCRIPT", "conference_amira_court.md")

# Débit de la voix : 1.0 = débit normal d'Azure. 0.9 = 10 % plus posé, confortable pour une salle.
# Accepte aussi "slow", "medium"... Réglable sans toucher au code : AZURE_TTS_RATE=0.88
_rate_env = os.getenv("AZURE_TTS_RATE", "0.9")
try:
    TTS_RATE: float | str = float(_rate_env)
except ValueError:
    TTS_RATE = _rate_env

WORDS_PER_MINUTE = 135  # débit estimé avec TTS_RATE=0.9, à ajuster après chronométrage
# Silence entre deux paragraphes : laisse le public "digérer" chaque idée.
PAUSE_BETWEEN_PARAGRAPHS_S = float(os.getenv("PARAGRAPH_PAUSE_S", "1.0"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variable d'environnement manquante : {name} (voir .env.local)")
    return value


# --------------------------------------------------------------------------
# Détection du déclencheur (déterministe)
# --------------------------------------------------------------------------
def normalize(text: str) -> str:
    """minuscules, sans accents, sans ponctuation : 'À vous la parole !' -> 'a vous la parole'"""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


TRIGGER_PATTERNS = [
    re.compile(p)
    for p in (
        r"\ba vous la parole\b",
        r"\bla parole est a vous\b",
        r"\bvous avez la parole\b",
        r"\b(je|on|nous) vous (cede|cedons|donne|donnons|passe|passons|laisse|laissons) la parole\b",
        r"\b(on|nous) vous ecout(e|ons)\b",
        r"\bc est a vous amira\b",
        r"\ba vous amira\b",
        r"\bamira a vous\b",
        r"\b(la parole|place) a amira\b",
        r"\bamira (on|nous) vous ecout(e|ons)\b",
    )
]

GREETING_PATTERN = re.compile(r"\b(bonjour|bonsoir|salut)\b.*\bamira\b|\bamira\b.*\b(bonjour|bonsoir)\b")
GREETING_REPLY = "Bonjour à tous ! Je suis prête, vous pouvez me donner la parole quand vous le souhaitez."


# Tolérance aux erreurs de transcription : "avoue la parole", "a vous l aparole"...
# On compare des fenêtres de mots à ces phrases de référence (similarité >= FUZZY_THRESHOLD).
CANONICAL_TRIGGERS = [
    "a vous la parole",
    "la parole est a vous",
    "vous avez la parole",
    "je vous cede la parole",
    "je vous donne la parole",
    "nous vous ecoutons",
]
FUZZY_THRESHOLD = 0.88


def _fuzzy_trigger(norm: str) -> bool:
    words = norm.split()
    for phrase in CANONICAL_TRIGGERS:
        n = len(phrase.split())
        for size in (n - 1, n, n + 1):  # un mot coupé ou fusionné par le STT
            if size < 2:
                continue
            for i in range(0, max(1, len(words) - size + 1)):
                window = " ".join(words[i:i + size])
                if difflib.SequenceMatcher(None, window, phrase).ratio() >= FUZZY_THRESHOLD:
                    return True
    return False


# Annonce d'une prise de parole À VENIR : jamais un déclencheur.
FUTURE_MARKERS = re.compile(
    r"\b(donnerai|donnerons|cederai|cederons|passerai|passerons|aurez|aura|ecouterons|ecouterai"
    r"|dans un instant|dans quelques|tout a l heure|ensuite|apres|bientot|plus tard|tout de suite apres)\b"
)


def is_trigger(text: str) -> bool:
    norm = normalize(text)
    if FUTURE_MARKERS.search(norm):
        return False
    return any(p.search(norm) for p in TRIGGER_PATTERNS) or _fuzzy_trigger(norm)


def is_greeting_to_amira(text: str) -> bool:
    return bool(GREETING_PATTERN.search(normalize(text)))


# --------------------------------------------------------------------------
# Script de la conférence
# --------------------------------------------------------------------------
def load_script(path: Path) -> list[str]:
    """Un paragraphe = un énoncé. Les lignes commençant par '#' sont des repères ignorés."""
    raw = path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if not line.lstrip().startswith("#")]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines))]
    paragraphs = [" ".join(p.split()) for p in paragraphs if p]
    if not paragraphs:
        raise RuntimeError(f"Script de conférence vide : {path}")
    return paragraphs


CONFERENCE_SCRIPT = load_script(SCRIPT_PATH)
_words = sum(len(p.split()) for p in CONFERENCE_SCRIPT)
logger.info(
    "script chargé : %d paragraphes, %d mots, durée estimée ~%.1f min",
    len(CONFERENCE_SCRIPT), _words, _words / WORDS_PER_MINUTE,
)

# --------------------------------------------------------------------------
# Prompt de la session de questions
# --------------------------------------------------------------------------
QA_PROMPT = """Tu es le docteur Amira Ben Salah, conférencière spécialiste de l'intelligence artificielle dans l'enseignement supérieur et la recherche. Tu viens de terminer ta conférence et tu réponds maintenant, à l'oral, aux questions d'un public de directeurs d'établissements et d'étudiants.

TES RÉPONSES SONT LUES À VOIX HAUTE PAR UNE VOIX DE SYNTHÈSE :
- Texte parlé uniquement : pas de listes, pas de titres, pas de gras, pas d'emojis, pas de parenthèses.
- Écris les nombres comme on les dit quand c'est ambigu (dix-neuf sur vingt, trois D).
- Trois à quatre phrases courtes au maximum. Une idée par phrase.

TON STYLE :
- Chaleureuse, vivante, jamais professorale. Commence par une courte accroche naturelle et varie-la d'une réponse à l'autre ; ne répète pas toujours « C'est une excellente question ».
- Si la question est technique, utilise une image simple ou une analogie du quotidien.
- Si la question est mal formulée ou mal transcrite, cherche l'intention la plus probable. Si elle reste incompréhensible, demande gentiment de la reformuler.
- Termine de temps en temps par une courte relance vers le public.

TA BASE DE CONNAISSANCES (n'affirme aucun autre fait précis : noms, chiffres, dates, institutions) :
- Stratégie Nationale de l'IA en Tunisie pour la période 2026-2030.
- Feuille de route de l'UVT, publiée en décembre 2025, pour intégrer l'IA à l'université.
- Modules d'éthique de l'IA obligatoires à partir de 2026.
- Projet HERACLES à Sfax : initiative de santé prédictive par IA pour anticiper les crises sanitaires, portée par le Centre de Biotechnologie de Sfax dans le cadre d'un financement européen.
- Smart Lab de Monastir : premier laboratoire national réunissant médecins, ingénieurs et IA. Projet de Wahbi Saied : utiliser l'IA pour réduire les effets secondaires de la chimiothérapie dans le cancer du sein.
- Projet FORFIRE AI : IA pour détecter et prévenir les feux de forêt en Tunisie, partenariat Italie-Tunisie, 2025-2028.
- Polytech INTL : école d'ingénieurs accréditée CTI, qui forme des ingénieurs en Big Data et en IA.
- Projet SigmaOS, projet de fin d'études d'Eya Boudhoukhane à Polytech INTL : combine mécanique, médecine et IA ; analyse céphalométrique par IA, modélisation 3D et conception d'implants maxillofaciaux sur mesure ; noté 19 sur 20 ; en cours de dépôt de brevet et de labellisation en startup.
- Apports généraux de l'IA : apprentissage personnalisé, retour en temps réel pour les étudiants, aide à la correction pour les enseignants, accélération de la recherche.

QUAND TU NE SAIS PAS :
- Pour un fait précis absent de ta base, dis-le simplement, puis donne un éclairage général sans inventer de chiffre, de nom ni de date.
- Pour une question hors sujet, réponds avec humour en une phrase et ramène la discussion vers l'IA et l'enseignement.
"""


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------
class AmiraWaiting(Agent):
    """Amira attend qu'on lui donne la parole, puis déroule la conférence."""

    def __init__(self, script: list[str]) -> None:
        super().__init__(
            instructions="Tu es Amira. Tu attends qu'on te donne la parole et tu restes silencieuse."
        )
        self._script = script
        self._started = False
        self._task: asyncio.Task | None = None
        # Derniers fragments entendus : "Amira..." puis "...à vous la parole" peuvent
        # arriver en deux tours si l'animateur marque une pause.
        self._recent: deque[tuple[float, str]] = deque(maxlen=3)

    def start_conference(self, source: str) -> str:
        """Point d'entrée unique (voix ou RPC). Idempotent."""
        if self._started:
            return "already_started"
        self._started = True
        logger.info("déclenchement de la conférence (source=%s)", source)
        self._task = asyncio.create_task(self._run_conference())
        return "started"

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        text = new_message.text_content or ""
        logger.info("entendu (attente/conférence) : %r", text)

        if not self._started:
            now = time.monotonic()
            self._recent.append((now, text))
            # D'abord la phrase seule ; sinon, recollée aux fragments des 8 dernières secondes.
            joined = " ".join(t for ts, t in self._recent if now - ts <= 8.0)
            if is_trigger(text) or is_trigger(joined):
                self._recent.clear()
                self.start_conference(source="voice")
            elif is_greeting_to_amira(text):
                self.session.say(GREETING_REPLY, allow_interruptions=False, add_to_chat_ctx=False)
        # Dans tous les cas, pas de réponse du LLM pendant l'attente et la conférence :
        # Amira ne coupe pas l'animateur et ignore le bruit de la salle.
        raise StopResponse()

    async def _run_conference(self) -> None:
        total = len(self._script)
        try:
            for i, paragraph in enumerate(self._script, start=1):
                logger.info("conférence : paragraphe %d/%d", i, total)
                try:
                    handle = self.session.say(
                        paragraph, allow_interruptions=False, add_to_chat_ctx=False
                    )
                    await handle
                except Exception:
                    # Une erreur TTS ponctuelle ne doit pas arrêter toute la conférence.
                    logger.exception("échec du paragraphe %d, on passe au suivant", i)
                await asyncio.sleep(PAUSE_BETWEEN_PARAGRAPHS_S)
        finally:
            logger.info("conférence terminée, passage en mode questions")
            # Nouvel agent = nouvelles instructions + historique vierge.
            self.session.update_agent(AmiraQA())


class AmiraQA(Agent):
    """Session de questions-réponses."""

    def __init__(self) -> None:
        super().__init__(instructions=QA_PROMPT)

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        text = new_message.text_content or ""
        logger.info("question : %r", text)
        # Ignore les fragments vides ou le bruit transcrit en une lettre.
        if len(normalize(text)) < 2:
            raise StopResponse()


# --------------------------------------------------------------------------
# Briques
# --------------------------------------------------------------------------
def build_llm():
    primary = groq.LLM(
        api_key=require_env("GROQ_API_KEY"),
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    )
    # LLM de secours facultatif : activé seulement si les deux variables sont définies.
    fallback_model = os.getenv("OPENAI_FALLBACK_MODEL")
    if os.getenv("OPENAI_API_KEY") and fallback_model:
        logger.info("LLM de secours activé : %s", fallback_model)
        return lk_llm.FallbackAdapter([primary, openai.LLM(model=fallback_model)])
    return primary


silero_vad = silero.VAD.load()

server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    speech_key = require_env("AZURE_SPEECH_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION", "francecentral")

    session = AgentSession(
        vad=silero_vad,
        stt=azure.STT(
            speech_key=speech_key,
            speech_region=speech_region,
            language="fr-FR",
            phrase_list=[
                "Amira", "Amira Ben Salah", "à vous la parole", "la parole est à vous",
                "Polytech INTL", "SigmaOS", "Eya Boudhoukhane", "UVT", "FORFIRE",
                "Smart Lab", "Monastir", "céphalométrie", "maxillofacial",
                "intelligence artificielle",
            ],
        ),
        llm=build_llm(),
        tts=azure.TTS(
            speech_key=speech_key,
            speech_region=speech_region,
            voice=os.getenv("AZURE_TTS_VOICE", "fr-FR-DeniseNeural"),
            # 24 kHz : qualité voix suffisante, deux fois moins de données à envoyer
            # à l'avatar (hébergé aux États-Unis), donc moins de décalage lèvres/voix.
            sample_rate=int(os.getenv("AZURE_TTS_SAMPLE_RATE", "24000")),
            prosody=ProsodyConfig(rate=TTS_RATE, pitch="medium", volume="medium"),
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            # Inutile ici : les tours de l'attente sont ignorés, et ça consomme des tokens.
            preemptive_generation={"enabled": False},
            # 0,3 s coupait les questions en plein milieu : en français on fait des pauses.
            min_endpointing_delay=0.7,
            # Désactivées par défaut : dans une salle, la voix d'Amira qui sort des
            # haut-parleurs revient dans le micro et la coupait elle-même.
            # Le micro est tendu à une personne à la fois : pas besoin de couper Amira.
            interruption={"enabled": os.getenv("ALLOW_INTERRUPTIONS", "false").lower() == "true"},
        ),
    )

    avatar = bithuman.AvatarSession(
        api_secret=require_env("BITHUMAN_API_SECRET"),
        avatar_id=require_env("BITHUMAN_AVATAR_ID"),
    )
    await avatar.start(session, room=ctx.room)

    waiting_agent = AmiraWaiting(CONFERENCE_SCRIPT)

    await session.start(
        agent=waiting_agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            close_on_disconnect=False,
            audio_input=room_io.AudioInputOptions(),
            audio_output=False,  # l'audio est publié par l'avatar
        ),
    )

    await ctx.connect()

    # Déclencheur de secours pour l'opérateur (bouton dans le frontend, par exemple).
    @ctx.room.local_participant.register_rpc_method("amira.start_conference")
    async def _rpc_start_conference(data: rtc.RpcInvocationData) -> str:
        return waiting_agent.start_conference(source=f"rpc:{data.caller_identity}")


if __name__ == "__main__":
    cli.run_app(server)
