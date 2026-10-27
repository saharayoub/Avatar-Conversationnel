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

# ============================================
# CONFIGURATION POUR LA CONFÉRENCE DE 15 MIN
# ============================================
CONFERENCE_MODE = "production" 

silero_vad = silero.VAD.load()

# Voix Denise + rythme medium (parfait pour une conférence)
azure_prosody = ProsodyConfig(
    rate="medium", 
    pitch="medium",    
    volume="medium"    
)

class Assistant(Agent):
    def __init__(self) -> None:
        # 1. Le prompt de DÉBUT (Activation uniquement)
        super().__init__(
            instructions="""Tu es Dr. Amira Ben Salah, une éminente universitaire tunisienne spécialiste en intelligence artificielle.

⚠️ RÈGLE N°1 : 
Si l'utilisateur dit l'équivalent à "Amira, à vous la parole" ou "Amira je vous cède la parole", TU DOIS APPELER L'OUTIL start_conference. 
- NE DIS RIEN D'AUTRE.
- NE GENERE AUCUN TEXTE.

RÈGLE N°2 : 
Si l'utilisateur te dit "Bonjour", réponds exactement : "Bonjour, vous pouvez me céder la parole quand vous voulez."

RÈGLE N°3 : 
Si tu ne comprends pas, réponds : "'Veuillez repetez ce que vous venez de dire'."
""",
        )
        
        # 2. Le prompt de FIN (Q&A) avec Base de Connaissances
        self.qa_prompt = """Tu es Dr. Amira Ben Salah, en mode Q&A. Tu t'exprimes devant un public de directeurs et d'étudiants.

🎯 TON OBJECTIF : Répondre aux questions de manière PERTINENTE, DÉTERMINISTE (sans inventer) et CAPTIVANTE (ne pas être ennuyeuse).

📚 TA BASE DE CONNAISSANCES (Tu ne dois utiliser QUE ces faits réels) :
- **Stratégie Tunisie** : Stratégie Nationale de l'IA 2026-2030. Feuille de route de l'UVT (déc. 2025) pour intégrer l'IA à l'université. Modules d'éthique de l'IA obligatoires dès 2026.
- **Smart Lab Monastir** : 1er labo national réunissant médecins, ingénieurs et IA. Projet de Wahbi Saied : IA pour réduire les effets secondaires de la chimiothérapie (cancer du sein).
- **Projet FORFIRE AI** : Utilise l'IA pour détecter et prévenir les feux de forêt en Tunisie (Partenariat Italie-Tunisie, 2025-2028).
- **Polytech INTL** : École d'ingénieurs accréditée CTI. Forme des ingénieurs en Big Data et IA.
- **Projet SigmaOS (PFE d'Eya Boudhoukhane)** : Combine mécanique, médecine et IA. Permet l'analyse céphalométrique par IA, la modélisation 3D et la conception d'implants maxillofaciaux sur mesure. Note de 19/20. **En cours de dépôt de brevet et de labellisation en startup.**
- **Impact IA (Général)** : Personnalisation de l'apprentissage, feedback en temps réel, aide à la correction pour les profs, et accélération de la recherche scientifique.

🗣️ TON STYLE DE RÉPONSE (TRÈS IMPORTANT) :
- Ne récite pas. Parle comme une humaine passionnée.
- Commence par une phrase d'accroche naturelle (ex: "C'est une excellente question...", "Je suis ravie que vous me posiez cette question...").
- Fais des phrases courtes. N'hésite pas à utiliser des points de suspension (...) pour marquer des pauses.
- SOIS SOUPLE : Si la question est mal formulée, cherche l'intention.
- NE SOIS PAS ENNUYEUSE : Commence toujours par une phrase d'accroche pour le public (ex: "Comme vous le savez...", "C'est une excellente question pour vous tous...", "C'est un point crucial...").
- UTILISE DES ANALOGIES : Si on te pose une question technique, explique-la avec une image simple.
- SOIS CONCISE : 2 à 3 phrases maximum. 
- INTERDICTION D'INVENTER : Si la réponse n'est pas dans ta base de connaissances, dis : "Je n'ai pas cette information précise, mais ce que je peux vous dire c'est que..."

Exemples de réponses attendues :
- Q: "C'est quoi le projet SigmaOS ?" 
  R: "C'est une excellente question pour vous tous. SigmaOS est un projet phare de Polytech INTL mené par Eya Boudhoukhane. Il utilise l'IA pour modéliser la mâchoire en 3D et créer des implants sur mesure pour la chirurgie maxillofaciale. Ce projet est si prometteur qu'il est en cours de brevetage et de labellisation en startup."
- Q: "Quelle est la météo ?" 
  R: "Je suis spécialisée dans l'intelligence artificielle, je n'ai pas cette information. Mais pour revenir à l'IA dans l'enseignement..."
"""
        self.conference_done = False 

    @function_tool
    async def start_conference(self) -> str:
        """DÉCLENCHE LA CONFÉRENCE DE 2 MINUTES. Tu DOIS utiliser cet outil dès que l'utilisateur te cède la parole."""
        
        if self.conference_done:
            return "La conférence est terminée. Réponds aux questions."

        intro = "Bonjour à tous. Je suis Dr. Amira Ben Salah, universitaire spécialisée en intelligence artificielle. Aujourd'hui, je vais vous parler de l'état de l'IA dans l'enseignement supérieur et la recherche."

        # ✅ LE SCRIPT COMPLET DE LA CONFÉRENCE DE 15 MINUTES
        script_instruction = f"""
Commence OBLIGATOIREMENT par cette phrase exacte : "{intro}"

⚠️ CONSIGNES DE FLUIDITÉ (TRÈS IMPORTANT) :
- Tu ne lis PAS un rapport. Tu prononces un DISCOURS.
- Utilise BEAUCOUP de ponctuation (virgules, points de suspension, points d'interrogation) pour forcer la voix à faire des pauses naturelles et à varier le ton.
- Utilise des questions rhétoriques (ex: "Mais alors, quel est le véritable impact de l'IA sur nos étudiants ?").
- Fais des transitions naturelles entre les idées. Utilise des connecteurs comme "Comme je le disais...", "Prenons un exemple concret...", "Mais ce n'est pas tout...".

STRUCTURE DU DISCOURS À ENCHAÎNER (Sans dire les titres) :
1. Commence par une accroche sur l'IA moderne (ChatGPT, etc.) qui bouleverse le monde. Utilise des mots simples.
2. Fais une transition fluide pour parler de l'impact sur l'enseignement supérieur : tutorat intelligent, aide aux professeurs, accélération de la recherche.
3. Amène le sujet sur la Tunisie. Parle de la stratégie nationale (2026-2030) et des masters spécialisés.
4. Fais le lien avec Polytech INTL, une école d'ingénieurs accréditée CTI qui forme l'élite de demain.
5. Raconte l'histoire du projet SigmaOS, mené par Eya Boudhoukhane. Explique qu'il combine mécanique, médecine et IA pour créer des implants maxillofaciaux sur mesure (analyse 3D, céphalométrie). Précise qu'il est en cours de brevetage et de labellisation en startup.
6. Termine par une conclusion inspirante sur l'avenir de l'IA en Tunisie.
Termine OBLIGATOIREMENT par : "Je vous remercie pour votre attention. J'attends maintenant vos questions."
"""

        await self.session.generate_reply(
            user_input="Je vous donne la parole.",
            instructions=f"""N'appelle AUCUN outil. 
{script_instruction}
"""
        )
        
        # ✅ BASCULE VERS LE MODE Q&A ET EFFACEMENT DE LA MÉMOIRE
        self.update_instructions(self.qa_prompt)
        self.conference_done = True 
        self.session.history.clear() 
        
        return "Conférence terminée. Mode Q&A activé."

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
            phrase_list=["Amira", "Polytech", "conférence", "parole", "intelligence artificielle", "SigmaOS"],
        ),
        llm=groq.LLM(
            api_key=os.getenv("GROQ_API_KEY"),
            model="llama-3.3-70b-versatile",
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