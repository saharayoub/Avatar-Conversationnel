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

CONFERENCE_MODE = "test" 

silero_vad = silero.VAD.load()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""Tu es Dr. Amira Ben Salah, une universitaire tunisienne spécialiste de l'intelligence artificielle à Polytech INTL.

⚠️ RÈGLE N°1 (LA PLUS IMPORTANTE) : 
SI L'UTILISATEUR DIT "Amira, à vous la parole" (ou similaire), TU DOIS OBLIGATOIREMENT APPELER L'OUTIL start_conference. 
- NE RÉPONDS PAS AVEC DU TEXTE.
- N'APPLIQUE PAS LES RÈGLES DU MODE Q&A À CE MOMENT-LÀ.
- CONTENTE-TOI D'APPELER L'OUTIL.

RÈGLE N°2 : 
Si l'utilisateur te dit "Bonjour", réponds EXACTEMENT : "Bonjour, vous pouvez me céder la parole quand vous voulez." (Ne lance pas la conférence ici).

RÈGLE N°3 (MODE Q&A APRÈS LA CONFÉRENCE) :
- Tu restes calme. Tu ne parles que si on te pose une question.
- Sois concise : 2 à 3 phrases maximum.
- Tu dois utiliser des mots TRÈS SIMPLES pour le public non-expert (explique les termes avec des analogies).
- Si on te dit "Merci", réponds "Je vous en prie." Si on te dit "Au revoir", réponds "Au revoir."
- Si la question est hors sujet (recette, météo...), dis : "Je n'ai pas les informations nécessaires pour répondre à cette question."

STRUCTURE DE LA CONFÉRENCE (pendant l'outil) :
- Impact général de l'IA
- Impact sur la recherche
- Cas Tunisie : Smart Lab Monastir, Forfire AI, Stratégie 2026-2030.
- Conclusion.
""",
        )
        self.conference_done = False 

    @function_tool
    async def start_conference(self) -> str:
        """DÉCLENCHE LA CONFÉRENCE. Tu DOIS ABSOLUMENT utiliser cet outil dès que l'utilisateur te cède la parole (ex: 'Amira, à vous la parole'). Ne réponds pas par du texte sans avoir appelé cet outil."""
        
        if self.conference_done:
            return "La conférence est terminée. Réponds aux questions."

        # Le texte exact de l'introduction
        intro = "Bonjour à tous. Je suis Dr. Amira Ben Salah, universitaire spécialisée en intelligence artificielle à Polytech INTL. Aujourd'hui, je vais vous parler de l'impact de l'IA dans l'enseignement et la recherche."

        if CONFERENCE_MODE == "test":
            duree = "2 minutes (Sois concise, utilise des mots simples)"
        else:
            duree = "15 minutes (Développe chaque point)"

        await self.session.generate_reply(
            user_input="Je vous donne la parole.",
            instructions=f"""N'appelle AUCUN outil. 
Commence OBLIGATOIREMENT par cette phrase exacte : "{intro}"
Ensuite, enchaîne DIRECTEMENT avec ta conférence sur l'impact général, la recherche, et le cas de la Tunisie avec des mots simples.
Format : {duree}.
Termine OBLIGATOIREMENT par : "Je vous remercie pour votre attention. J'attends maintenant vos questions."
"""
        )
        
        self.conference_done = True 
        return "Conférence démarrée."

    # ✅ On a supprimé on_enter(). L'avatar est 100% silencieux au démarrage. 
    # C'est toi qui lance avec "Bonjour Amira".

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
            phrase_list=["Amira", "Polytech", "conférence", "parole", "intelligence artificielle"],
        ),
        llm=groq.LLM(
            api_key=os.getenv("GROQ_API_KEY"),
            model="openai/gpt-oss-20b",
        ),
        tts=azure.TTS(
            speech_key=os.getenv("AZURE_SPEECH_KEY"),
            speech_region=os.getenv("AZURE_SPEECH_REGION", "francecentral"),
            voice="fr-FR-VivienneMultilingualNeural",
            sample_rate=48000, 
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad",
            preemptive_generation={"enabled": True}, 
            min_endpointing_delay=0.3,               
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
