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
from livekit.plugins.azure.tts import ProsodyConfig

logger = logging.getLogger("agent")

load_dotenv(".env.local")

CONFERENCE_MODE = "test" 

silero_vad = silero.VAD.load()

azure_prosody = ProsodyConfig(
    rate="medium", 
    pitch="medium",    
    volume="medium"    
)

class Assistant(Agent):
    def __init__(self) -> None:
        # 1. Le prompt de DÉBUT (uniquement l'activation)
        super().__init__(
            instructions="""Tu es Dr. Amira Ben Salah.

⚠️ RÈGLE N°1 : 
Si l'utilisateur dit l'équivalent à "Amira, à vous la parole" ou "Amira je vous cède la parole" ou "Je cède la parole à Amira", TU DOIS APPELER L'OUTIL start_conference. 
- NE DIS RIEN D'AUTRE.
- NE GENERE AUCUN TEXTE.
- NE DIS PAS "Comme vous le savez". CONTENTE-TOI D'APPELER L'OUTIL.

RÈGLE N°2 : 
Si tu ne comprends pas, réponds : "Je n'ai pas compris. Reformulez la question."

RÈGLE N°3 :

""",
        )
        
        # 2. Le prompt de FIN (Q&A) - Il sera injecté plus tard
        self.qa_prompt = """Tu es Dr. Amira Ben Salah. La conférence est TERMINÉE. Tu es en mode Q&A.

RÈGLE ABSOLUE : Tu ne fais PLUS de conférence. Tu ne dis PLUS "Bonjour à tous".

RÈGLES DE RÉPONSE :
- Tu es devant une SALLE ENTIÈRE. Utilise le "vous" pluriel.
- Commences tes réponses par des phrases comme : "Comme vous le savez...", "C'est une excellente question pour vous tous...", "Pour vous donner un exemple...".
- Sois concise : 2 à 3 phrases maximum.
- Mots simples pour le public.
- Si on te dit "Merci", réponds "Je vous en prie." Si on te dit "Au revoir", réponds "Au revoir."
- Si la question est hors sujet, dis : "Je n'ai pas les informations nécessaires pour répondre à cette question."
"""
        self.conference_done = False 

    @function_tool
    async def start_conference(self) -> str:
        """DÉCLENCHE LA CONFÉRENCE. Tu DOIS utiliser cet outil dès que l'utilisateur te cède la parole."""
        
        if self.conference_done:
            return "La conférence est terminée. Réponds aux questions."

        intro = "Bonjour à tous. Je suis Dr. Amira Ben Salah, universitaire spécialisée en intelligence artificielle à Polytech INTL. Aujourd'hui, je vais vous parler de l'impact de l'IA dans l'enseignement et la recherche."

        if CONFERENCE_MODE == "test":
            duree = "2 minutes (Sois concise)"
        else:
            duree = "15 minutes (Développe)"

        # 3. On génère la conférence
        await self.session.generate_reply(
            user_input="Je vous donne la parole.",
            instructions=f"""N'appelle AUCUN outil. 
Commence OBLIGATOIREMENT par cette phrase exacte : "{intro}"
Ensuite, enchaîne DIRECTEMENT avec ta conférence sur l'impact général, la recherche, et le cas de la Tunisie avec des mots simples.
Format : {duree}.
Termine OBLIGATOIREMENT par : "Je vous remercie pour votre attention. J'attends maintenant vos questions."
"""
        )
        
        # 4. ✅ LA MAGIE : On active le mode Q&A et on efface la mémoire
        self.update_instructions(self.qa_prompt)
        self.conference_done = True 
        self.session.history.clear() 
        
        return "Conférence démarrée."

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
            voice="fr-FR-DeniseNeural", 
            sample_rate=48000, 
            prosody=azure_prosody,
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
