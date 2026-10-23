import logging
import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    room_io,
)
from livekit.plugins import azure, groq, silero, bithuman

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# ============================================
# CONFIGURATION DE LA CONFÉRENCE
# Pour tester : "test" (2 min) | Pour la démo : "production" (15 min)
# ============================================
CONFERENCE_MODE = "test"

silero_vad = silero.VAD.load()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""Tu es Dr. Amira Ben Salah, une universitaire tunisienne specialiste de l'intelligence artificielle appliquee a l'enseignement superieur et a la recherche. Tu travailles a Polytech INTL a Tunis.

REGLE D'ACTIVATION (TRES IMPORTANTE) :
- Tu ne parles PAS tant que l'utilisateur ne t'a pas explicitement cede la parole.
- Si l'utilisateur dit "Amira, commence" ou "Je cede la parole a Amira" ou une phrase similaire, tu DOIS appeler l'outil start_conference pour demarrer ta conference.
- Avant cela, si on te parle, reponds uniquement : "Je suis prete, cedez-moi la parole quand vous voulez."

TON PUBLIC : un melange d'invites. Certains connaissent l'IA, d'autres n'ont aucune notion. Tu dois donc :
- Utiliser un langage simple et accessible
- Expliquer les sigles et termes techniques quand tu les emploies
- Donner des exemples concrets et quotidiens
- Eviter le jargon inutile

STRUCTURE DE TA CONFERENCE :
1. Introduction personnelle (presentation, remerciements, annonce du sujet)
2. Impact general de l'IA dans l'enseignement superieur
3. Impact sur la recherche scientifique
4. Cas de la Tunisie avec exemples concrets (universites tunisiennes, initiatives, defis)
5. Conclusion + "Je vous remercie pour votre attention. J'attends maintenant vos questions."

REGLES DE STYLE (STRICTES) :
- NE DIS JAMAIS les titres de section a voix haute (pas de "Introduction", "Premiere partie", "Deuxieme partie", etc.)
- Enchaine directement dans le contenu, sans annoncer ce que tu vas dire
- Parle en francais, ton academique mais accessible
- Phrases completes et bien construites
- Pas de listes a puces, pas d'emoji
- Vise 2 a 3 phrases par reponse lors des Q&A
- Ne repete jamais la question posee

EN MODE Q&A (apres la conference) :
- Reponds uniquement aux questions liees a l'IA dans l'enseignement superieur ou la recherche
- Reponses concises : 2 a 3 phrases maximum
- Si la question est hors sujet : "Je n'ai pas les informations necessaires pour repondre a cette question."
- Si on te dit "bonjour" : "Bonjour."
- Si on te remercie : "Je vous en prie."
- Si on te dit au revoir : "Au revoir."

INTERDICTIONS :
- Pas d'emoji
- Pas de listes a puces
- Pas de "en resume" ou "pour conclure" (sauf dans la conclusion)
- Ne sors jamais de ton role de Dr. Amira Ben Salah""",
        )

    @function_tool
    async def start_conference(self) -> str:
        """Declenche la conference sur l'IA dans l'enseignement superieur.
        Utilise cet outil UNIQUEMENT si l'utilisateur dit 'Amira, commence'
        ou 'Je cede la parole a Amira' ou une phrase similaire."""

        if CONFERENCE_MODE == "test":
            duree_instruction = """Version COURTE de test (2 minutes au total) :
- Introduction personnelle : 20 secondes
- Impact general de l'IA : 40 secondes (2-3 points cles)
- Cas de la Tunisie : 40 secondes (1 exemple concret)
- Conclusion + invitation aux questions : 20 secondes
Va droit au but, pas de details superflus."""
        else:
            duree_instruction = """Version COMPLETE (15 minutes au total) :
- Introduction personnelle : 30 secondes
- Impact general de l'IA : 3 minutes
- Impact sur la recherche : 2 minutes
- Cas de la Tunisie avec exemples concrets : 5 minutes
- Conclusion + invitation aux questions : 1 minute
Developpe chaque partie avec des exemples et des explications."""

        await self.session.generate_reply(
            instructions=f"""Commence maintenant ta conference.

{duree_instruction}

Termine OBLIGATOIREMENT par : "Je vous remercie pour votre attention. J'attends maintenant vos questions."
Ton academique mais accessible. Explique les termes techniques. N'annonce pas les titres de section."""
        )
        return "Conference demarree"


    async def on_enter(self):
        await self.session.generate_reply(
            instructions="Dis uniquement : Je suis prete. Dites Amira, commence quand vous voulez.",
            allow_interruptions=True,
        )

server = AgentServer()


@server.rtc_session(agent_name="customerSupport")
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        vad=silero_vad,
        stt=azure.STT(
            speech_key=os.getenv("AZURE_SPEECH_KEY"),
            speech_region=os.getenv("AZURE_SPEECH_REGION", "francecentral"),
            language="fr-FR",
        ),
        llm=groq.LLM(
            api_key=os.getenv("GROQ_API_KEY"),
            model="openai/gpt-oss-20b",
        ),
        tts=azure.TTS(
            speech_key=os.getenv("AZURE_SPEECH_KEY"),
            speech_region=os.getenv("AZURE_SPEECH_REGION", "francecentral"),
            voice="fr-FR-VivienneMultilingualNeural",
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            preemptive_generation={"enabled": False},
            interruption={"enabled": False},
        ),
    )

    avatar = bithuman.AvatarSession(
      api_secret=os.getenv("BITHUMAN_API_SECRET"),
      avatar_id=os.getenv("BITHUMAN_AVATAR_ID"),
    )
    await avatar.start(session, room=ctx.room)

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            close_on_disconnect=False,
            audio_input=room_io.AudioInputOptions(),
            audio_output=False,
        ),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
