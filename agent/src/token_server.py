"""
Serveur local de l'interface spectateur.

- GET /       : sert frontend/index.html (et le logo).
- GET /token  : génère un jeton LiveKit frais pour chaque connexion
                et, si Amira n'est pas déjà dans la salle, crée le dispatch.

Les clés API restent ici, côté serveur (lues dans .env.local) : le navigateur
ne reçoit qu'un jeton limité à la salle, valable quelques heures.

Lancement :  uv run python src/token_server.py   puis ouvrir http://localhost:8080
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

SRC_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = SRC_DIR.parent.parent / "frontend"
load_dotenv(SRC_DIR.parent / ".env.local")

logger = logging.getLogger("token_server")

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
ROOM_NAME = os.getenv("ROOM_NAME", "ma-salle-test")
AGENT_NAME = os.getenv("AGENT_NAME", "customerSupport")  # même nom que dans agent.py
# AUTO_DISPATCH=false pour revenir au dispatch manuel (lk dispatch create).
AUTO_DISPATCH = os.getenv("AUTO_DISPATCH", "true").lower() == "true"
PORT = int(os.getenv("FRONTEND_PORT", "8080"))

# Deux spectateurs qui arrivent en même temps ne doivent pas créer deux dispatchs.
_dispatch_lock = asyncio.Lock()


async def ensure_agent(lkapi: api.LiveKitAPI) -> str:
    """Crée le dispatch seulement si aucune Amira n'est présente ni en attente."""
    async with _dispatch_lock:
        try:
            res = await lkapi.room.list_participants(
                api.ListParticipantsRequest(room=ROOM_NAME)
            )
            participants = res.participants
        except api.TwirpError as e:
            if e.code != "not_found":  # salle inexistante = personne dedans
                raise
            participants = []

        if any(p.kind == api.ParticipantInfo.Kind.AGENT for p in participants):
            return "already_present"

        try:
            dispatches = await lkapi.agent_dispatch.list_dispatch(ROOM_NAME)
        except api.TwirpError as e:
            if e.code != "not_found":
                raise
            dispatches = []
        # Dispatch fait avant le démarrage du worker : il sera repris, ne pas en refaire.
        if any(d.agent_name == AGENT_NAME for d in dispatches):
            return "already_pending"

        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=ROOM_NAME)
        )
        logger.info("dispatch créé : %s -> %s", AGENT_NAME, ROOM_NAME)
        return "dispatched"


async def handle_token(request: web.Request) -> web.Response:
    # Identité unique : deux onglets avec la même identité se déconnecteraient l'un l'autre.
    identity = f"spectateur-{uuid.uuid4().hex[:6]}"
    token = (
        api.AccessToken()
        .with_identity(identity)
        .with_name("spectateur")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM_NAME))
        .to_jwt()
    )

    agent_status = "manual"
    if AUTO_DISPATCH:
        try:
            agent_status = await ensure_agent(request.app["lkapi"])
        except Exception:
            # Le spectateur peut quand même se connecter ; l'opérateur fera le dispatch à la main.
            logger.exception("échec de la vérification / du dispatch de l'agent")
            agent_status = "error"

    logger.info("jeton délivré à %s (agent : %s)", identity, agent_status)
    return web.json_response(
        {"url": LIVEKIT_URL, "token": token, "agent": agent_status},
        headers={"Cache-Control": "no-store"},
    )


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(FRONTEND_DIR / "index.html")


async def on_startup(app: web.Application) -> None:
    app["lkapi"] = api.LiveKitAPI()  # lit LIVEKIT_URL / API_KEY / API_SECRET


async def on_cleanup(app: web.Application) -> None:
    await app["lkapi"].aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/token", handle_token)
    app.router.add_static("/", FRONTEND_DIR)  # logo et autres fichiers
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    logger.info(
        "interface : http://localhost:%d  (salle=%s, agent=%s, dispatch auto=%s)",
        PORT, ROOM_NAME, AGENT_NAME, AUTO_DISPATCH,
    )
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
