"""
Amira - conférencière virtuelle (LiveKit Agents + bitHuman).

Machine à états simple et déterministe :

    ATTENTE  --("à vous la parole")-->  CONFÉRENCE  --(fin du script)-->  QUESTIONS
    (silence)                           (script, non interruptible)       (LLM, interactif)

    QUESTIONS  --("place à Sahar")-->  PAUSE  --("Amira, je vous laisse conclure")-->  CONCLUSION
                                       (écoute, ne répond jamais)                       (script, puis silence)

- Les déclencheurs sont détectés par le CODE (expressions régulières sur la transcription),
  pas par le LLM : pas de faux départ, pas de silence si le LLM ne suit pas la consigne.
- La conférence et la conclusion sont des SCRIPTS écrits à l'avance (.md), lus par le TTS
  paragraphe par paragraphe : durée maîtrisée, aucun risque d'invention ni de coupure LLM.
- Le LLM ne sert qu'à la session de questions-réponses.
- Chaque transition a aussi un bouton de secours dans la console opérateur (RPC "amira.*").
- Après la conclusion, Amira reste silencieuse pendant le débat.
"""

import asyncio
import contextlib
import difflib
import logging
import os
import re
import time
import unicodedata
from collections import deque
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
# Par défaut : la version 10 minutes, celle de l'atelier inaugural.
SCRIPT_PATH = SRC_DIR / os.getenv("CONFERENCE_SCRIPT", "conference_amira_10min.md")
# Conclusion lue après l'intervention de Sahar, juste avant le débat.
CONCLUSION_PATH = SRC_DIR / os.getenv("CONCLUSION_SCRIPT", "conclusion_amira.md")

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


def _fuzzy_match(norm: str, phrases: list[str], threshold: float = FUZZY_THRESHOLD) -> bool:
    words = norm.split()
    for phrase in phrases:
        n = len(phrase.split())
        for size in (n - 1, n, n + 1):  # un mot coupé ou fusionné par le STT
            if size < 2:
                continue
            for i in range(0, max(1, len(words) - size + 1)):
                window = " ".join(words[i:i + size])
                if difflib.SequenceMatcher(None, window, phrase).ratio() >= threshold:
                    return True
    return False


def _fuzzy_trigger(norm: str) -> bool:
    return _fuzzy_match(norm, CANONICAL_TRIGGERS)


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


# QUESTIONS -> PAUSE : on passe la parole à Sahar.
# Le STT peut écrire "Sahar", "Sahara", "Sarah" ou "Sara" : on accepte les quatre.
_SAHAR = r"(?:sahar\w*|sarah?)"
PAUSE_PATTERNS = [
    re.compile(p)
    for p in (
        rf"\b(la parole|place) (est )?a {_SAHAR}\b",  # "place à Sahar", "je passe la parole à Sahar"
        rf"\b(a vous|c est a vous|a toi|c est a toi) {_SAHAR}\b",
        rf"\b{_SAHAR} (a vous|a toi|vous avez la parole|la parole est a (vous|toi))\b",
    )
]


def is_pause_trigger(text: str) -> bool:
    norm = normalize(text)
    if FUTURE_MARKERS.search(norm):
        return False
    return any(p.search(norm) for p in PAUSE_PATTERNS)


# PAUSE -> CONCLUSION : Sahar rend la parole à Amira pour conclure.
# Volontairement strict : "pour conclure" seul (fréquent dans un exposé) ne déclenche PAS.
_HANDOVER_VERB = r"(je|on|nous) (vous|te) (laisse|laissons|cede|cedons|donne|donnons|passe|passons)"
CONCLUSION_PATTERNS = [
    re.compile(p)
    for p in (
        rf"\b{_HANDOVER_VERB} (le soin de )?(conclure|la conclusion|le mot de la fin)\b",
        r"\ba (vous|toi) de conclure\b",
        r"\b(le mot de la fin|la conclusion) (revient )?a amira\b",
        r"\bamira (concluez|conclus|vous pouvez conclure|tu peux conclure)\b",
    )
]
CANONICAL_CONCLUSION = ["je vous laisse conclure", "a vous de conclure"]
# Seuil plus strict : "je vous laisse continuer" ne doit pas passer pour "conclure".
CONCLUSION_FUZZY_THRESHOLD = 0.92


def is_conclusion_trigger(text: str) -> bool:
    norm = normalize(text)
    if FUTURE_MARKERS.search(norm):
        return False
    return any(p.search(norm) for p in CONCLUSION_PATTERNS) or _fuzzy_match(
        norm, CANONICAL_CONCLUSION, CONCLUSION_FUZZY_THRESHOLD
    )


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
CONCLUSION_SCRIPT = load_script(CONCLUSION_PATH)
for _name, _script in (("conférence", CONFERENCE_SCRIPT), ("conclusion", CONCLUSION_SCRIPT)):
    _words = sum(len(p.split()) for p in _script)
    logger.info(
        "%s chargée : %d paragraphes, %d mots, durée estimée ~%.1f min",
        _name, len(_script), _words, _words / WORDS_PER_MINUTE,
    )


async def read_script(session: AgentSession, paragraphs: list[str], label: str) -> None:
    """Lit un script paragraphe par paragraphe, sans interruption possible par la salle."""
    total = len(paragraphs)
    for i, paragraph in enumerate(paragraphs, start=1):
        logger.info("%s : paragraphe %d/%d", label, i, total)
        try:
            await session.say(paragraph, allow_interruptions=False, add_to_chat_ctx=False)
        except Exception:
            # Une erreur TTS ponctuelle ne doit pas arrêter toute la lecture.
            logger.exception("%s : échec du paragraphe %d, on passe au suivant", label, i)
        await asyncio.sleep(PAUSE_BETWEEN_PARAGRAPHS_S)

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
    """ATTENTE puis CONFÉRENCE : Amira attend qu'on lui donne la parole, puis lit le script."""

    def __init__(self, script: list[str]) -> None:
        super().__init__(
            instructions="Tu es Amira. Tu attends qu'on te donne la parole et tu restes silencieuse."
        )
        self._script = script
        self._started = False
        self._task: asyncio.Task | None = None
        # État suivant quand la conférence se termine ou est annulée :
        # "qa" (normal ou "passer aux questions"), "waiting" (arrêt opérateur), "none" (clôture).
        self._after = "qa"
        # Derniers fragments entendus : "Amira..." puis "...à vous la parole" peuvent
        # arriver en deux tours si l'animateur marque une pause.
        self._recent: deque[tuple[float, str]] = deque(maxlen=3)

    def _conference_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _cancel_conference(self, after: str) -> None:
        self._after = after
        if self._task and not self._task.done():
            self._task.cancel()
        # Coupe aussi la phrase en cours de lecture (sinon elle irait jusqu'au bout).
        with contextlib.suppress(RuntimeError):
            self.session.interrupt(force=True)

    def start_conference(self, source: str) -> str:
        """Point d'entrée unique (voix ou RPC). Idempotent."""
        if self._started:
            return "already_started"
        self._started = True
        self._after = "qa"
        logger.info("déclenchement de la conférence (source=%s)", source)
        self._task = asyncio.create_task(self._run_conference())
        return "started"

    def stop_conference(self) -> str:
        """Arrête la conférence et remet Amira en attente."""
        if not self._conference_running():
            self._started = False
            return "not_running"
        logger.info("conférence arrêtée par l'opérateur")
        self._cancel_conference(after="waiting")
        return "stopped"

    def skip_to_qa(self) -> str:
        """Passe directement au mode questions."""
        logger.info("passage direct aux questions par l'opérateur")
        if self._conference_running():
            self._cancel_conference(after="qa")  # le bloc finally fera la transition
        else:
            self._started = True
            self.session.update_agent(AmiraQA())
        return "skipped"

    def cancel_for_shutdown(self) -> None:
        if self._conference_running():
            self._cancel_conference(after="none")

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
        try:
            await read_script(self.session, self._script, "conférence")
        finally:
            if self._after == "qa":
                logger.info("conférence terminée, passage en mode questions")
                # Nouvel agent = nouvelles instructions + historique vierge.
                self.session.update_agent(AmiraQA())
            elif self._after == "waiting":
                logger.info("retour en attente")
                self.session.update_agent(AmiraWaiting(self._script))


class AmiraQA(Agent):
    """QUESTIONS : session de questions-réponses (seul état où le LLM parle)."""

    def __init__(self) -> None:
        super().__init__(instructions=QA_PROMPT)

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        text = new_message.text_content or ""
        logger.info("question : %r", text)
        # "Place à Sahar" : on passe en pause au lieu de répondre.
        if is_pause_trigger(text):
            logger.info("passage en pause (source=voice)")
            self.session.update_agent(AmiraPause())
            raise StopResponse()
        # Ignore les fragments vides ou le bruit transcrit en une lettre.
        if len(normalize(text)) < 2:
            raise StopResponse()


class AmiraPause(Agent):
    """PAUSE : Sahar présente le projet. Amira écoute sans jamais répondre,
    et guette seulement la phrase qui lui rend la parole pour conclure."""

    def __init__(self) -> None:
        super().__init__(instructions="Tu es Amira. Sahar parle : tu restes silencieuse.")
        self._recent: deque[tuple[float, str]] = deque(maxlen=3)

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        text = new_message.text_content or ""
        logger.info("entendu (pause) : %r", text)
        now = time.monotonic()
        self._recent.append((now, text))
        joined = " ".join(t for ts, t in self._recent if now - ts <= 6.0)
        if is_conclusion_trigger(text) or is_conclusion_trigger(joined):
            logger.info("déclenchement de la conclusion (source=voice)")
            self.session.update_agent(AmiraConclusion(CONCLUSION_SCRIPT))
        raise StopResponse()


class AmiraConclusion(Agent):
    """CONCLUSION : lit le script de conclusion, puis reste silencieuse pendant le débat."""

    def __init__(self, script: list[str]) -> None:
        super().__init__(instructions="Tu es Amira. Tu as conclu : tu restes silencieuse.")
        self._script = script
        self._task: asyncio.Task | None = None

    async def on_enter(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        await read_script(self.session, self._script, "conclusion")
        logger.info("conclusion terminée : Amira reste silencieuse pendant le débat")

    def cancel_for_shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        logger.info("entendu (conclusion/débat) : %r", new_message.text_content or "")
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
                "intelligence artificielle", "Sahar", "place à Sahar",
                "je vous laisse conclure", "à vous de conclure",
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

    await session.start(
        agent=AmiraWaiting(CONFERENCE_SCRIPT),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            close_on_disconnect=False,
            audio_input=room_io.AudioInputOptions(),
            audio_output=False,  # l'audio est publié par l'avatar
        ),
    )

    await ctx.connect()

    # ----- Boutons de secours de la console opérateur -----
    # Chaque commande agit sur l'état COURANT d'Amira (session.current_agent) :
    # après un changement d'état, l'ancien objet agent n'est plus utilisable.
    lp = ctx.room.local_participant

    @lp.register_rpc_method("amira.start_conference")
    async def _rpc_start_conference(data: rtc.RpcInvocationData) -> str:
        agent = session.current_agent
        if not isinstance(agent, AmiraWaiting):
            return "refusé : Amira n'est pas en attente"
        return agent.start_conference(source=f"rpc:{data.caller_identity}")

    @lp.register_rpc_method("amira.greet")
    async def _rpc_greet(data: rtc.RpcInvocationData) -> str:
        session.say(GREETING_REPLY, allow_interruptions=False, add_to_chat_ctx=False)
        return "greeted"

    @lp.register_rpc_method("amira.stop_conference")
    async def _rpc_stop(data: rtc.RpcInvocationData) -> str:
        agent = session.current_agent
        if not isinstance(agent, AmiraWaiting):
            return "refusé : aucune conférence en cours"
        return agent.stop_conference()

    @lp.register_rpc_method("amira.skip_to_qa")
    async def _rpc_skip_qa(data: rtc.RpcInvocationData) -> str:
        agent = session.current_agent
        if not isinstance(agent, AmiraWaiting):
            return "refusé : Amira n'est plus en attente ni en conférence"
        return agent.skip_to_qa()

    @lp.register_rpc_method("amira.pause")
    async def _rpc_pause(data: rtc.RpcInvocationData) -> str:
        agent = session.current_agent
        if isinstance(agent, AmiraPause):
            return "already_paused"
        if not isinstance(agent, AmiraQA):
            return "refusé : la pause se lance depuis le mode questions"
        logger.info("passage en pause (source=rpc:%s)", data.caller_identity)
        session.update_agent(AmiraPause())
        return "paused"

    @lp.register_rpc_method("amira.conclusion")
    async def _rpc_conclusion(data: rtc.RpcInvocationData) -> str:
        agent = session.current_agent
        if isinstance(agent, AmiraConclusion):
            return "already_started"
        # Depuis la pause (cas normal) ou les questions (si la pause a été oubliée).
        if not isinstance(agent, AmiraPause | AmiraQA):
            return "refusé : la conclusion se lance après la conférence"
        logger.info("déclenchement de la conclusion (source=rpc:%s)", data.caller_identity)
        session.interrupt(force=True)  # coupe une éventuelle réponse en cours
        session.update_agent(AmiraConclusion(CONCLUSION_SCRIPT))
        return "started"

    @lp.register_rpc_method("amira.end_session")
    async def _rpc_end(data: rtc.RpcInvocationData) -> str:
        logger.info("clôture forcée de la session par l'opérateur")
        agent = session.current_agent
        if isinstance(agent, AmiraWaiting | AmiraConclusion):
            agent.cancel_for_shutdown()
        # Fermeture en arrière-plan : la réponse RPC doit partir avant la déconnexion.
        ctx.proc.userdata["closing_task"] = asyncio.create_task(session.aclose())
        return "session_closed"


if __name__ == "__main__":
    cli.run_app(server)
