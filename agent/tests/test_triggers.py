"""Déclencheurs vocaux de la machine à états (détection par regex, sans LLM)."""

import pytest

from agent import is_conclusion_trigger, is_pause_trigger, is_trigger


@pytest.mark.parametrize(
    "text",
    [
        "Merci Amira. Place à Sahar !",
        "Je passe maintenant la parole à Sahar.",
        "La parole est à Sarah.",
        "Sahar, à vous.",
        "C'est à vous, Sahar",
    ],
)
def test_pause_trigger(text: str) -> None:
    assert is_pause_trigger(text)


@pytest.mark.parametrize(
    "text",
    [
        "Est-ce que vous pouvez nous parler du projet SigmaOS ?",
        "Ensuite, je donnerai la parole à Sahar.",  # annonce au futur
        "Qui a créé Amira ?",
    ],
)
def test_no_pause_trigger(text: str) -> None:
    assert not is_pause_trigger(text)


@pytest.mark.parametrize(
    "text",
    [
        "Amira, je vous laisse conclure.",
        "Je vous laisse le mot de la fin, Amira.",
        "Amira, à vous de conclure !",
        "Le mot de la fin revient à Amira.",
        "il me semble juste de laisser le dernier mot à celle qui a fait le travail. Amira, je vous laisse conclure.",
    ],
)
def test_conclusion_trigger(text: str) -> None:
    assert is_conclusion_trigger(text)


@pytest.mark.parametrize(
    "text",
    [
        # Phrases plausibles dans l'exposé de Sahar : ne doivent PAS lancer la conclusion.
        "Pour conclure, ce projet m'a appris une leçon essentielle.",
        "Ce que vous avez vu à l'écran, c'est Amira. Pour conclure sur l'architecture...",
        "Je vous laisse continuer la lecture du rapport.",
        "Gardez toujours le dernier mot.",
        "Tout à l'heure, je vous laisserai conclure, Amira.",
    ],
)
def test_no_conclusion_trigger(text: str) -> None:
    assert not is_conclusion_trigger(text)


def test_start_trigger_unchanged() -> None:
    assert is_trigger("Amira, à vous la parole !")
    assert not is_trigger("Dans un instant, je vous donnerai la parole.")
