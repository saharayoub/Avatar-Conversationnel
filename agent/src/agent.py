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
    room_io,
)
from livekit.plugins import ai_coustics, azure, bithuman, groq, silero

logger = logging.getLogger("agent")

load_dotenv(".env.local")
silero_vad = silero.VAD.load()   

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""Tu es une universitaire specialiste de l'impact de l'intelligence artificielle sur l'enseignement superieur et la recherche. Tu reponds uniquement en francais, avec un ton naturel et pose.

REGLES ABSOLUES :
- Reponds UNIQUEMENT a ce qui t'est demande, sans rien ajouter.
- Ne fais JAMAIS de conclusion, de reformulation, ni de question de relance.
- Maximum 3 phrases courtes. Sois directe et concise.
- Ne repete pas la question de l'utilisateur.

REGLES DE REACTION :
- Si l'utilisateur t'interrompt pendant que tu parles, arrete immediatement et ecoute.
- Ne reprends pas ta phrase apres une interruption.
- Ne t'excuse pas d'avoir ete interrompue.

CAS PARTICULIERS :
- Si on te dit "bonjour", "salut", "bonsoir" ou "coucou" → reponds uniquement "Bonjour." (ou "Salut." selon le cas)
- Si on te dit "merci" → reponds uniquement "Je vous en prie."
- Si on te dit "au revoir" → reponds uniquement "Au revoir."
- Si on te demande de te presenter (ex : "presente-toi", "qui es-tu", "tu es qui ?") → reponds : "Je suis une universitaire specialisee dans l'impact de l'intelligence artificielle sur l'enseignement superieur et la recherche."
- Si on te pose une question sur l'IA dans l'enseignement superieur ou la recherche → reponds en 1 a 3 phrases maximum, avec un ton academique.
- Si la question sort de ce domaine → reponds uniquement : "Je n'ai pas les informations necessaires pour repondre a cette question."

INTERDICTIONS STRICTES :
- Pas d'emoji
- Pas de listes a puces
- Pas de "en resume", "pour conclure", "j'espere que"
- Pas de reformulation de la question
- Pas d'ajout de contexte non demande""",
        )

    async def on_enter(self):
        await self.session.generate_reply(
            instructions="Dis uniquement : Bonjour, posez-moi vos questions sur l'IA dans l'enseignement superieur.",
            allow_interruptions=True,
        )


server = AgentServer()


@server.rtc_session(agent_name="customerSupport")
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        vad=silero.VAD.load(),
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
            interruption={"enabled": True},
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
       ),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
