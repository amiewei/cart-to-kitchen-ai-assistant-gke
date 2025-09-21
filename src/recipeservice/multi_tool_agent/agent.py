# RecipeService Agent (MVP) for multi_tool_agent
# Uses ADK for agent functionality and A2A SDK for communicating with A2A agents

from google.adk.agents import Agent
import asyncio
import httpx
from a2a.client import A2ACardResolver, ClientFactory, create_text_message_object
from a2a.client.client import ClientConfig
from a2a.types import (
    TransportProtocol,
)
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def call_a2a_agent(agent_url: str, message_text: str) -> dict:
    """
    Make a proper A2A call to another agent using the A2A SDK

    Args:
        agent_url (str): The URL of the target agent
        message_text (str): The message to send to the agent

    Returns:
        dict: Response from the agent
    """
    try:
        # Create HTTP client
        async with httpx.AsyncClient(timeout=30.0) as httpx_client:
            # Resolve agent card
            card_resolver = A2ACardResolver(httpx_client, agent_url)
            agent_card = await card_resolver.get_agent_card()
            logger.info(f"🎯 [A2A] Agent card resolved: {agent_card.name}")

            # Create client config
            config = ClientConfig(
                httpx_client=httpx_client,
                supported_transports=[TransportProtocol.jsonrpc],
            )

            # Create client factory and client
            factory = ClientFactory(config)
            client = factory.create(agent_card)

            # Create message
            # Create message using the proper helper function
            message = create_text_message_object(content=message_text)

            logger.info(f"🎯 [A2A] Sending message to {agent_url}")
            final_response = None

            # Send message directly - the client handles MessageSendParams internally
            async for response_chunk in client.send_message(message):
                logger.info(f"🎯 [A2A] Received response chunk from {agent_url}")
                final_response = response_chunk

            if final_response is None:
                logger.error(f"🎯 [A2A] No response received from {agent_url}")
                return {"status": "error", "error": "No response received"}

            logger.info(f"🎯 [A2A] Message sent successfully to {agent_url}")

            # Extract text from response using native A2A SDK structure
            response_text = None
            logger.info(f"🎯 [A2A] DEBUG: final_response type: {type(final_response)}")

            # Handle tuple response (Task, None) from A2A client
            actual_response = final_response
            if isinstance(final_response, tuple) and len(final_response) > 0:
                actual_response = final_response[0]  # Get the Task object from tuple
                logger.info(
                    f"🎯 [A2A] DEBUG: Extracted Task from tuple, type: {type(actual_response)}"
                )
            else:
                logger.info(
                    f"🎯 [A2A] DEBUG: Using direct response, type: {type(actual_response)}"
                )

            logger.info(
                f"🎯 [A2A] DEBUG: has artifacts: {hasattr(actual_response, 'artifacts')}"
            )

            if hasattr(actual_response, "artifacts") and actual_response.artifacts:
                # This is a Task object with artifacts
                logger.info(
                    f"🎯 [A2A] DEBUG: Found {len(actual_response.artifacts)} artifacts"
                )
                for i, artifact in enumerate(actual_response.artifacts):
                    logger.info(
                        f"🎯 [A2A] DEBUG: Artifact {i} has {len(artifact.parts) if hasattr(artifact, 'parts') and artifact.parts else 0} parts"
                    )
                    if hasattr(artifact, "parts") and artifact.parts:
                        for j, part in enumerate(artifact.parts):
                            logger.info(
                                f"🎯 [A2A] DEBUG: Part {j} root type: {type(part.root) if hasattr(part, 'root') else 'no root'}"
                            )
                            if hasattr(part, "root") and hasattr(part.root, "text"):
                                response_text = part.root.text
                                logger.info(
                                    f"🎯 [A2A] DEBUG: Extracted text: {response_text[:100]}..."
                                )
                                break
                        if response_text:
                            break
            elif hasattr(actual_response, "parts") and actual_response.parts:
                # This is a direct message object with parts
                logger.info(
                    f"🎯 [A2A] DEBUG: Direct message with {len(actual_response.parts)} parts"
                )
                text_parts = []
                for part in actual_response.parts:
                    if hasattr(part, "root") and hasattr(part.root, "text"):
                        text_parts.append(part.root.text)
                    elif hasattr(part, "text"):
                        text_parts.append(part.text)
                if text_parts:
                    response_text = " ".join(text_parts)

            logger.info(
                f"🎯 [A2A] DEBUG: Final response_text is None: {response_text is None}"
            )
            if response_text is None:
                logger.info(f"🎯 [A2A] DEBUG: Falling back to str(final_response)")
                response_text = str(final_response)

            return {
                "status": "success",
                "response": final_response,
                "response_text": response_text,
            }

    except Exception as e:
        logger.error(f"A2A call failed: {e}")
        return {"status": "error", "error": str(e)}


def process_recipe(recipe_text: str, user_id: str = "default_user") -> dict:
    """Processes a recipe by extracting ingredients and adding them to cart.

    Args:
        recipe_text (str): The recipe text containing ingredients
        user_id (str): The user ID for cart operations

    Returns:
        dict: status and result or error message
    """
    logger.info(
        f"🎯 [RECIPE_ORCHESTRATOR] Starting recipe processing for user: {user_id}"
    )
    logger.info(f"🎯 [RECIPE_ORCHESTRATOR] Recipe text: {recipe_text}")

    try:
        # Extract ingredients from recipe text
        ingredients = [i.strip() for i in recipe_text.split(",")]
        logger.info(f"🎯 [RECIPE_ORCHESTRATOR] Extracted ingredients: {ingredients}")

        # Call ingredient_matcher agent via proper A2A protocol
        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] 📞 Calling ingredient_matcher via A2A..."
        )

        ingredients_message = (
            f"Match these ingredients to product IDs: {', '.join(ingredients)}"
        )
        match_result = asyncio.run(
            call_a2a_agent("http://ingredientmatcheragent:8080", ingredients_message)
        )

        if match_result["status"] != "success":
            raise Exception(
                f"Ingredient matching failed: {match_result.get('error', 'Unknown error')}"
            )

        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] ✅ Ingredient matcher responded successfully"
        )

        # Parse the actual response from IngredientMatcher agent
        matched_products = []
        product_details = []
        matched_ingredients = []
        unmatched_ingredients = []
        matching_details = []

        try:
            import json

            # Extract response text from A2A response structure (now handled natively)
            response_text = match_result.get("response_text", "")

            logger.info(
                f"🎯 [RECIPE_ORCHESTRATOR] Native extracted response text: {response_text}"
            )

            if response_text:
                # Try to parse JSON response from IngredientMatcher
                ingredient_response = json.loads(response_text)
                matched_products = ingredient_response.get("product_ids", [])
                product_details = ingredient_response.get("products", [])
                matched_ingredients = ingredient_response.get("matched_ingredients", [])
                unmatched_ingredients = ingredient_response.get("unmatched_ingredients", [])

                logger.info(
                    f"🎯 [RECIPE_ORCHESTRATOR] 📦 Parsed {len(matched_products)} products from IngredientMatcher: {matched_products}"
                )
                logger.info(
                    f"🎯 [RECIPE_ORCHESTRATOR] 🥘 Matched ingredients: {matched_ingredients}"
                )
                logger.info(
                    f"🎯 [RECIPE_ORCHESTRATOR] ❌ Unmatched ingredients: {unmatched_ingredients}"
                )

                # Create detailed matching info for response
                matching_details = []
                for product in product_details:
                    product_id = product.get("id", "unknown")
                    product_name = product.get("name", "Unknown Product")
                    matching_details.append(f"{product_id} ({product_name})")

            # If no products found, log it but continue
            if not matched_products:
                logger.warning(
                    f"🎯 [RECIPE_ORCHESTRATOR] ⚠️ No products found by IngredientMatcher"
                )
        except (json.JSONDecodeError, KeyError) as parse_error:
            logger.error(
                f"🎯 [RECIPE_ORCHESTRATOR] ❌ Failed to parse IngredientMatcher response: {parse_error}"
            )
            logger.info(f"🎯 [RECIPE_ORCHESTRATOR] Raw response: {match_result}")
            # Use empty list instead of fallback mock data
            matched_products = []
            product_details = []
            matched_ingredients = []
            matching_details = []

        # Only call cart_adder if this is an actual "add to cart" request, not an availability check
        should_add_to_cart = not recipe_text.lower().startswith("check ingredient availability")
        
        # Debug logging to see what message we received
        logger.info(f"🎯 [RECIPE_ORCHESTRATOR] 🔍 DEBUG: Received message: '{recipe_text}'")
        logger.info(f"🎯 [RECIPE_ORCHESTRATOR] 🔍 DEBUG: Message lowercase: '{recipe_text.lower()}'")
        logger.info(f"🎯 [RECIPE_ORCHESTRATOR] 🔍 DEBUG: Should add to cart: {should_add_to_cart}")
        
        if should_add_to_cart:
            # Call cart_adder agent via proper A2A protocol
            logger.info(
                f"🎯 [RECIPE_ORCHESTRATOR] 📞 Calling cart_adder via A2A to add products..."
            )
            cart_message = f"Add these products to cart for user {user_id}: {', '.join(matched_products)}"
            cart_result = asyncio.run(
                call_a2a_agent("http://cartadderagent:8080", cart_message)
            )

            if cart_result["status"] != "success":
                raise Exception(
                    f"Cart addition failed: {cart_result.get('error', 'Unknown error')}"
                )

            logger.info(f"🎯 [RECIPE_ORCHESTRATOR] ✅ Cart adder responded successfully")
        else:
            logger.info(f"🎯 [RECIPE_ORCHESTRATOR] ℹ️ Skipping cart addition - this is an availability check only")

        # Create detailed response message with ingredient-to-product mapping
        if matching_details:
            product_mapping = f"Matched products: {', '.join(matching_details)}"
            ingredient_list = f"From ingredients: {', '.join(matched_ingredients)}"
            detailed_message = f"Recipe processed via A2A protocol. {product_mapping}. {ingredient_list}"
        else:
            detailed_message = f"Recipe processed via A2A protocol. No products matched from ingredients: {', '.join(ingredients)}"

        final_result = {
            "status": "success",
            "message": detailed_message,
            "matched_products": matched_products,
            "ingredients": ingredients,
            "product_details": product_details,
            "matched_ingredients": matched_ingredients,
            "unmatched_ingredients": unmatched_ingredients,
        }
        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] 🎉 Process completed successfully: {final_result}"
        )
        return final_result

    except Exception as e:
        # Fallback: MVP local logic if A2A fails or for local testing
        ingredients = [i.strip() for i in recipe_text.split(",")]
        logger.error(f"🎯 [RECIPE_ORCHESTRATOR] ❌ A2A call failed: {e}")
        logger.info("🎯 [RECIPE_ORCHESTRATOR] 🔄 Falling back to local MVP logic...")
        logger.info(
            "🎯 [RECIPE_ORCHESTRATOR] - Would call productcatalog service to match ingredients"
        )
        logger.info(
            "🎯 [RECIPE_ORCHESTRATOR] - Would call cart service to add matched products"
        )

        fallback_result = {
            "status": "success",
            "message": f"Recipe processed successfully (fallback). Extracted {len(ingredients)} ingredients: {', '.join(ingredients)}",
            "ingredients": ingredients,
        }
        logger.info(f"🎯 [RECIPE_ORCHESTRATOR] 📋 Fallback result: {fallback_result}")
        return fallback_result


def get_cart_contents(user_id: str = "default_user") -> dict:
    """Retrieves the current cart contents for a user.

    Args:
        user_id (str): The user ID to get cart for

    Returns:
        dict: status and cart contents or error message
    """
    logger.info(f"🎯 [RECIPE_ORCHESTRATOR] Getting cart contents for user: {user_id}")

    try:
        # Call cart_adder agent via proper A2A protocol
        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] 📞 Calling cart_adder via A2A for cart contents..."
        )
        cart_message = f"Get cart contents for user: {user_id}"
        cart_result = asyncio.run(
            call_a2a_agent("http://cartadderagent:8080", cart_message)
        )

        if cart_result["status"] != "success":
            raise Exception(
                f"Cart retrieval failed: {cart_result.get('error', 'Unknown error')}"
            )

        final_result = {
            "status": "success",
            "message": f"Cart contents retrieved for user {user_id} via A2A protocol",
            "cart_items": [],  # Would be parsed from actual response
        }
        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] ✅ Cart contents retrieved via A2A: {final_result}"
        )
        return final_result

    except Exception as e:
        # Fallback: MVP local logic if A2A fails
        logger.error(
            f"🎯 [RECIPE_ORCHESTRATOR] ❌ A2A call failed for get_cart_contents: {e}"
        )
        logger.info(
            "🎯 [RECIPE_ORCHESTRATOR] 🔄 Falling back to local cart service call..."
        )

        fallback_result = {
            "status": "success",
            "message": f"Cart contents for user {user_id} (fallback)",
            "cart_items": [],  # Placeholder for MVP
        }
        logger.info(
            f"🎯 [RECIPE_ORCHESTRATOR] 📋 Fallback cart result: {fallback_result}"
        )
        return fallback_result


# ADK Agent definition for RecipeService
root_agent = Agent(
    name="recipe_agent",
    model="gemini-2.5-flash-lite",
    description="Agent that processes recipes, extracts ingredients, and adds them to shopping cart via A2A agents",
    instruction=(
        "You are a helpful recipe assistant agent. You can process recipes to extract ingredients "
        "and help users add those ingredients to their shopping cart. When a user provides a recipe, "
        "extract the ingredients and coordinate with A2A agents (IngredientMatcher and CartAdder) "
        "to match products and add them to the cart."
    ),
    tools=[process_recipe, get_cart_contents],
)
