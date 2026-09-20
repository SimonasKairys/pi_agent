"""Paleidimo taškas piagent Telegram botui."""

from __future__ import annotations

import logging
import os
from agent.config import load_users
from agent.llm import LLMClient
from agent.telegram_ui import create_application

logging.basicConfig(level=logging.INFO)
# httpx žurnale rašo pilnus URL, kuriuose yra Telegram boto tokenas
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Trūksta aplinkos kintamojo TELEGRAM_BOT_TOKEN")

    # Tikrinama paleidimo metu: be rakto kiekviena žinutė gautų tik klaidos pranešimą
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("Trūksta aplinkos kintamojo OPENROUTER_API_KEY")

    # Patikrinama konfigūracija paleidimo metu
    users = load_users()
    logger.info("Užkrauta leistinų vartotojų: %d", len(users))

    # Vienas klientas visam procesui: kitaip kiekviena žinutė kurtų naują ryšių telkinį
    app = create_application(token, llm_client=LLMClient())
    logger.info("Botas paleidžiamas...")
    app.run_polling()


if __name__ == "__main__":
    main()
