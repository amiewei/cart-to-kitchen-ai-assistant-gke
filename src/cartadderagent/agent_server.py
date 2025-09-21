#!/usr/bin/env python3
"""
Cart Adder Agent Server
Uses proper A2A SDK for agent-to-agent communication
"""
import logging
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    TaskState,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError
import json
import grpc
import os
import asyncio

# Import generated proto files
import demo_pb2
import demo_pb2_grpc

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CartAdderExecutor(AgentExecutor):
    """Agent executor for cart operations using CartService gRPC"""

    def __init__(self):
        super().__init__()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute the cart operation task"""
        try:
            # Get user input from context
            user_message = context.get_user_input()
            logger.info(f"🛒 [CART_ADDER] Processing: {user_message}")

            # Get or create task
            task = context.current_task
            if not task:
                task = new_task(context.message)
                await event_queue.enqueue_event(task)

            # Process the cart request
            response_text = await self.process_cart_request(user_message)

            # Send completion event
            from a2a.utils import completed_task, new_artifact
            from a2a.types import Part, TextPart

            completed = completed_task(
                task.id,
                task.context_id,
                [
                    new_artifact(
                        [Part(root=TextPart(text=response_text))],
                        "cart_operation_result",
                    )
                ],
                [context.message],
            )
            await event_queue.enqueue_event(completed)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Send error status
            from a2a.server.tasks import TaskUpdater

            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            await updater.update_status(
                TaskState.failed,
                new_agent_text_message(
                    f"Error processing cart request: {str(e)}",
                    context.context_id,
                    context.task_id,
                ),
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the execution - not supported"""
        raise ServerError(error=UnsupportedOperationError())

    async def process_cart_request(self, text: str) -> str:
        """Process cart-related requests and interact with CartService via gRPC"""
        try:
            logger.info(f"🛒 [CART_ADDER] Processing text: {text}")

            # Parse the input text to extract product information
            product_data = self._parse_product_data(text)
            logger.info(f"🛒 [CART_ADDER] Extracted product data: {product_data}")

            if not product_data.get("product_ids"):
                return json.dumps(
                    {
                        "error": "No product IDs found in message",
                        "message": text,
                        "added_items": [],
                    }
                )

            # Add products to cart using CartService
            user_id = product_data.get("user_id", "default_user")
            added_items = []

            for product_id in product_data["product_ids"]:
                success = await self._add_item_to_cart(user_id, product_id, 1)
                if success:
                    added_items.append(product_id)

            # Get updated cart contents
            cart_contents = await self._get_cart_contents(user_id)

            result = {
                "status": "success",
                "user_id": user_id,
                "added_items": added_items,
                "total_cart_items": len(cart_contents) if cart_contents else 0,
                "cart_contents": (
                    cart_contents[:5] if cart_contents else []
                ),  # Show first 5 items
                "message": f"Successfully added {len(added_items)} items to cart for user {user_id}",
            }

            logger.info(f"🛒 [CART_ADDER] Final result: {result}")
            return json.dumps(result)

        except Exception as e:
            logger.error(f"Error in cart processing: {e}")
            return json.dumps({"error": str(e), "added_items": []})

    def _parse_product_data(self, text: str) -> dict:
        """Parse product information from input text"""
        try:
            # Try to parse as JSON first (from IngredientMatcher)
            if text.strip().startswith("{"):
                data = json.loads(text)
                if "product_ids" in data:
                    return {
                        "product_ids": data["product_ids"],
                        "user_id": data.get("user_id", "default_user"),
                        "source": "ingredient_matcher",
                    }

            # Fallback: extract product IDs from text patterns
            import re

            # Look for product ID patterns (e.g., CHICKEN001, GARLIC001)
            product_pattern = r"\b[A-Z]+\d+\b"
            product_ids = re.findall(product_pattern, text)

            # Look for user ID in text
            user_id = "default_user"
            user_match = re.search(r"user[:\s]+([a-zA-Z0-9\-]+)", text, re.IGNORECASE)
            if user_match:
                user_id = user_match.group(1)

            return {
                "product_ids": list(set(product_ids)),  # Remove duplicates
                "user_id": user_id,
                "source": "text_parsing",
            }

        except Exception as e:
            logger.error(f"Error parsing product data: {e}")
            return {"product_ids": [], "user_id": "default_user", "source": "error"}

    async def _add_item_to_cart(
        self, user_id: str, product_id: str, quantity: int = 1
    ) -> bool:
        """Add item to cart using CartService gRPC"""
        try:
            # Connect to CartService
            service_host = os.environ.get("CART_SERVICE_HOST", "cartservice")
            service_port = os.environ.get("CART_SERVICE_PORT", "7070")
            channel_address = f"{service_host}:{service_port}"

            logger.info(
                f"🛒 [CART_ADDER] Connecting to CartService at {channel_address}"
            )

            with grpc.insecure_channel(channel_address) as channel:
                stub = demo_pb2_grpc.CartServiceStub(channel)

                # Create cart item
                cart_item = demo_pb2.CartItem(product_id=product_id, quantity=quantity)

                # Create add item request
                request = demo_pb2.AddItemRequest(user_id=user_id, item=cart_item)

                # Add item to cart
                response = stub.AddItem(request)
                logger.info(
                    f"🛒 [CART_ADDER] Successfully added {product_id} to cart for user {user_id}"
                )
                return True

        except Exception as e:
            logger.error(
                f"Error adding item {product_id} to cart for user {user_id}: {e}"
            )
            return False

    async def _get_cart_contents(self, user_id: str) -> list:
        """Get cart contents using CartService gRPC"""
        try:
            # Connect to CartService
            service_host = os.environ.get("CART_SERVICE_HOST", "cartservice")
            service_port = os.environ.get("CART_SERVICE_PORT", "7070")
            channel_address = f"{service_host}:{service_port}"

            with grpc.insecure_channel(channel_address) as channel:
                stub = demo_pb2_grpc.CartServiceStub(channel)

                # Create get cart request
                request = demo_pb2.GetCartRequest(user_id=user_id)

                # Get cart contents
                response = stub.GetCart(request)

                cart_items = []
                for item in response.items:
                    cart_items.append(
                        {"product_id": item.product_id, "quantity": item.quantity}
                    )

                logger.info(
                    f"🛒 [CART_ADDER] Retrieved {len(cart_items)} items from cart for user {user_id}"
                )
                return cart_items

        except Exception as e:
            logger.error(f"Error getting cart contents for user {user_id}: {e}")
            return []


def get_agent_card(host: str, port: int) -> AgentCard:
    """Create agent card for the cart adder"""
    import os

    skills = [
        AgentSkill(
            id="add_to_cart",
            name="Add to Cart",
            description="Adds products to a user's shopping cart",
            tags=["cart", "shopping", "products"],
            examples=[
                "Add products to cart for user john: product_123, product_456",
                "Add these items to my cart: product_789",
            ],
        ),
        AgentSkill(
            id="get_cart_contents",
            name="Get Cart Contents",
            description="Retrieves the contents of a user's shopping cart",
            tags=["cart", "shopping", "retrieve"],
            examples=["Get cart contents for user john", "Show me what's in my cart"],
        ),
    ]

    # Use Kubernetes service name if available, otherwise fall back to host:port
    service_name = os.environ.get("SERVICE_NAME", "cartadderagent")
    service_port = os.environ.get("SERVICE_PORT", "8080")
    agent_url = f"http://{service_name}:{service_port}/"

    logger.info(f"🛒 [CART_ADDER] Agent card URL: {agent_url}")

    return AgentCard(
        name="Cart Adder Agent",
        description="Manages shopping cart operations using A2A protocol",
        url=agent_url,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(
            input_modes=["text"], output_modes=["text"], streaming=False
        ),
        skills=skills,
    )


def main():
    """Main function to start the Cart Adder Agent server"""
    host = "0.0.0.0"
    port = 8080

    logger.info(f"Starting Cart Adder Agent on {host}:{port}")

    # Create agent card
    agent_card = get_agent_card(host, port)

    # Create task store
    task_store = InMemoryTaskStore()

    # Create agent executor
    agent_executor = CartAdderExecutor()

    # Create request handler
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=task_store,
    )

    # Create A2A server application
    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    # Build and run the server
    app = server.build()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
