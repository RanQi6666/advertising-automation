from backend.app.integrations.image.gateway_provider import (
    aclose_shared_gateway_image_clients,
)
from backend.app.integrations.llm.responses_provider import (
    aclose_shared_gateway_text_clients,
)


async def aclose_shared_gateway_clients() -> None:
    await aclose_shared_gateway_text_clients()
    await aclose_shared_gateway_image_clients()
