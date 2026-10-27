#!/usr/bin/env python3
"""Send a text task to a discovered A2A agent and print its protocol response."""

from __future__ import annotations

import argparse
import asyncio

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest


async def send(base_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=1300) as http_client:
        card = await A2ACardResolver(httpx_client=http_client, base_url=base_url).get_agent_card()
        print(f"Discovered: {card.name} ({card.supported_interfaces[0].protocol_version})")
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False))
        try:
            request = SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))
            async for response in client.send_message(request):
                if hasattr(response, "model_dump_json"):
                    print(response.model_dump_json(indent=2))
                else:
                    print(response)
        finally:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("message")
    args = parser.parse_args()
    asyncio.run(send(args.base_url, args.message))


if __name__ == "__main__":
    main()
