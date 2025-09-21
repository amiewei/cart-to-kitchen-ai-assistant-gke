#!/usr/bin/env python3
"""
Ingredient Matcher Agent Server
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
import sys
import os
import httpx
import asyncio

# Import generated proto files
import demo_pb2
import demo_pb2_grpc

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IngredientMatcherExecutor(AgentExecutor):
    """Agent executor for ingredient matching"""

    def __init__(self):
        super().__init__()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute the ingredient matching task"""
        try:
            # Get user input from context
            user_message = context.get_user_input()
            logger.info(f"🔍 [INGREDIENT_MATCHER] Processing: {user_message}")

            # Get or create task
            task = context.current_task
            if not task:
                task = new_task(context.message)
                await event_queue.enqueue_event(task)

            # Process the message and match ingredients
            response_text = await self.match_ingredients_from_text(user_message)

            # Send completion event
            from a2a.utils import completed_task, new_artifact
            from a2a.types import Part, TextPart

            completed = completed_task(
                task.id,
                task.context_id,
                [
                    new_artifact(
                        [Part(root=TextPart(text=response_text))],
                        "ingredient_match_result",
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
                    f"Error matching ingredients: {str(e)}",
                    context.context_id,
                    context.task_id,
                ),
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the execution - not supported"""
        raise ServerError(error=UnsupportedOperationError())

    async def match_ingredients_from_text(self, text: str) -> str:
        """Extract ingredients from text and match to product IDs using ProductCatalogService"""
        try:
            logger.info(f"🔍 [INGREDIENT_MATCHER] Processing text: {text}")

            # Parse ingredients from the input text
            ingredients = self._parse_ingredients(text)
            logger.info(f"🔍 [INGREDIENT_MATCHER] Extracted ingredients: {ingredients}")

            if not ingredients:
                return json.dumps(
                    {
                        "error": "No ingredients found in message",
                        "message": text,
                        "matched_products": [],
                    }
                )

            # Match ingredients to products using ProductCatalogService
            matched_products = []
            unmatched_ingredients = []
            
            for ingredient in ingredients:
                products = await self._search_products(ingredient)
                if products:
                    matched_products.extend(products)
                else:
                    unmatched_ingredients.append(ingredient)

            result = {
                "matched_ingredients": [ingredient for ingredient in ingredients if ingredient not in unmatched_ingredients],
                "unmatched_ingredients": unmatched_ingredients,
                "product_ids": [p["id"] for p in matched_products],
                "products": matched_products,
                "message": f"Successfully matched {len(ingredients) - len(unmatched_ingredients)} of {len(ingredients)} ingredients to {len(matched_products)} products",
            }

            logger.info(f"🔍 [INGREDIENT_MATCHER] Final result: {result}")
            return json.dumps(result)

        except Exception as e:
            logger.error(f"Error in ingredient matching: {e}")
            return json.dumps({"error": str(e), "matched_products": []})

    def _parse_ingredients(self, text: str) -> list:
        """Parse ingredients from text input and clean modifier words"""
        import re
        
        def clean_ingredient_name(ingredient: str) -> str:
            """Remove cooking modifiers and preparation words from ingredient names"""
            # List of modifiers to remove
            modifiers = [
                # Preparation methods
                r'\b(?:fresh|grated|chopped|diced|sliced|minced|crushed|ground|whole)\b',
                r'\b(?:thinly|finely|coarsely|roughly)\s+(?:sliced|chopped|diced|minced)\b',
                r'\b(?:cut\s+into\s+(?:strips|pieces|chunks))\b',
                r'\b(?:mixed|large|small|medium)\b',
                # Remove "into strips", "thinly sliced", etc.
                r'\s+(?:cut\s+into\s+strips|thinly\s+sliced|finely\s+chopped)\b',
            ]
            
            cleaned = ingredient
            for modifier_pattern in modifiers:
                cleaned = re.sub(modifier_pattern, '', cleaned, flags=re.IGNORECASE)
            
            # Clean up extra spaces and capitalize properly
            cleaned = ' '.join(cleaned.split())  # Remove extra spaces
            return cleaned.strip()

        # Map of search terms to actual product names in the catalog
        ingredient_mapping = {
            # Meat & Poultry
            "chicken breast": "Chicken Breast",
            "chicken": "Chicken Breast",
            "ground beef": "Ground Beef",
            "beef": "Ground Beef",
            "salmon fillets": "Salmon Fillets",
            "salmon": "Salmon Fillets",
            # Vegetables & Fresh
            "garlic": "Garlic",
            "ginger": "Ginger",  # Add ginger mapping
            "bell pepper": "Bell Peppers",
            "bell peppers": "Bell Peppers",
            "peppers": "Bell Peppers",
            "yellow onion": "Yellow Onion",
            "onion": "Yellow Onion",
            "onions": "Yellow Onion",
            "roma tomatoes": "Roma Tomatoes",
            "tomatoes": "Roma Tomatoes",
            "tomato": "Roma Tomatoes",
            "lettuce": "Lettuce",
            "carrots": "Carrots",
            "carrot": "Carrots",
            "celery": "Celery",
            "cucumber": "Cucumber",
            "mixed greens": "Mixed Greens",
            "avocado": "Avocado",
            # Dairy & Eggs
            "cheddar cheese": "Cheddar Cheese",
            "cheese": "Cheddar Cheese",
            "whole milk": "Whole Milk",
            "milk": "Whole Milk",
            "large eggs": "Large Eggs",
            "eggs": "Large Eggs",
            # Pantry & Grains
            "flour tortillas": "Flour Tortillas",
            "tortillas": "Flour Tortillas",
            "whole wheat bread": "Whole Wheat Bread",
            "bread": "Whole Wheat Bread",
            "jasmine rice": "Jasmine Rice",
            "rice": "Jasmine Rice",
            "egg noodles": "Egg Noodles",
            "noodles": "Egg Noodles",
            # Oils & Condiments
            "olive oil": "Olive Oil",
            "sesame oil": "Sesame Oil",  # Add sesame oil mapping
            "vegetable oil": "Vegetable Oil",  # Add vegetable oil mapping
            "soy sauce": "Soy Sauce",
            "chicken broth": "Chicken Broth",
            "vegetable broth": "Vegetable Broth",  # Add vegetable broth mapping
            "broth": "Chicken Broth",
            # Spices & Herbs
            "sea salt": "Sea Salt",
            "salt": "Sea Salt",
            "fresh thyme": "Fresh Thyme",
            "thyme": "Fresh Thyme",
            "fresh dill": "Fresh Dill",
            "dill": "Fresh Dill",
            "taco seasoning": "Taco Seasoning",
            # Shells & Specialty
            "taco shells": "Taco Shells",
            # Fruits
            "bananas": "Bananas",
            "banana": "Bananas",
            "lemon": "Lemon",
            "lemons": "Lemon",
        }

        found_ingredients = []
        
        # Extract comma-separated ingredients using regex
        import re
        
        # Extract the ingredient list part after "cart (serves X):" or "Check ingredient availability:"
        match = re.search(r'cart \(serves \d+\):\s*(.+)', text, re.IGNORECASE)
        if not match:
            # Try the "Check ingredient availability:" pattern
            match = re.search(r'check ingredient availability:\s*(.+)', text, re.IGNORECASE)
        
        if match:
            ingredient_text = match.group(1)
            # Split by comma and clean up
            raw_ingredients = [item.strip() for item in ingredient_text.split(',')]
            
            for raw_ingredient in raw_ingredients:
                # Remove quantity prefixes like "2 cups", "1 lb", etc.
                clean_ingredient = re.sub(r'^\d+(?:\.\d+)?\s+(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|pound|lbs?|lb|ounces?|ounce|oz|pieces?|piece|cloves?|clove|slices?|slice|cans?|can|packages?|package|pkg)\s+', '', raw_ingredient, flags=re.IGNORECASE)
                
                # Clean modifiers from ingredient name
                clean_ingredient = clean_ingredient_name(clean_ingredient)
                
                if clean_ingredient:
                    # Check if this matches any of our mapped ingredients (case insensitive)
                    normalized_ingredient = clean_ingredient.lower()
                    mapped_ingredient = None
                    
                    # Try exact match first
                    if normalized_ingredient in ingredient_mapping:
                        mapped_ingredient = ingredient_mapping[normalized_ingredient]
                    else:
                        # Try partial matches (search term contained in ingredient)
                        for search_term, product_name in ingredient_mapping.items():
                            if search_term in normalized_ingredient:
                                mapped_ingredient = product_name
                                break
                    
                    if mapped_ingredient:
                        if mapped_ingredient not in found_ingredients:
                            found_ingredients.append(mapped_ingredient)
                    else:
                        # Add cleaned ingredient as-is to preserve it for processing
                        if clean_ingredient not in found_ingredients:
                            found_ingredients.append(clean_ingredient)
        else:
            # Fallback: if no specific pattern found, try to extract from comma-separated text
            # This handles cases where the input format might be different
            if ',' in text:
                raw_ingredients = [item.strip() for item in text.split(',')]
                for raw_ingredient in raw_ingredients:
                    # Remove quantity prefixes
                    clean_ingredient = re.sub(r'^\d+(?:\.\d+)?\s+(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|pound|lbs?|lb|ounces?|ounce|oz|pieces?|piece|cloves?|clove|slices?|slice|cans?|can|packages?|package|pkg)\s+', '', raw_ingredient, flags=re.IGNORECASE)
                    clean_ingredient = clean_ingredient_name(clean_ingredient)
                    
                    if clean_ingredient:
                        # Check mappings
                        normalized_ingredient = clean_ingredient.lower()
                        mapped_ingredient = None
                        
                        if normalized_ingredient in ingredient_mapping:
                            mapped_ingredient = ingredient_mapping[normalized_ingredient]
                        else:
                            for search_term, product_name in ingredient_mapping.items():
                                if search_term in normalized_ingredient:
                                    mapped_ingredient = product_name
                                    break
                        
                        if mapped_ingredient:
                            if mapped_ingredient not in found_ingredients:
                                found_ingredients.append(mapped_ingredient)
                        else:
                            if clean_ingredient not in found_ingredients:
                                found_ingredients.append(clean_ingredient)

        logger.info(f"🔍 [INGREDIENT_MATCHER] Extracted ingredients: {found_ingredients}")
        return found_ingredients

    async def _search_products(self, ingredient: str) -> list:
        """Search for products matching an ingredient using ProductCatalogService gRPC"""
        try:
            # Connect to ProductCatalogService
            service_host = os.environ.get(
                "PRODUCTCATALOG_SERVICE_HOST", "productcatalogservice"
            )
            service_port = os.environ.get("PRODUCTCATALOG_SERVICE_PORT", "3550")
            channel_address = f"{service_host}:{service_port}"

            logger.info(
                f"🔍 [INGREDIENT_MATCHER] Connecting to ProductCatalog at {channel_address}"
            )

            with grpc.insecure_channel(channel_address) as channel:
                stub = demo_pb2_grpc.ProductCatalogServiceStub(channel)

                # Search for products matching the ingredient
                request = demo_pb2.SearchProductsRequest(query=ingredient)
                response = stub.SearchProducts(request)

                products = []
                for product in response.results:
                    products.append(
                        {
                            "id": product.id,
                            "name": product.name,
                            "description": product.description,
                            "price": f"${product.price_usd.units}.{product.price_usd.nanos//10000000:02d}",
                            "categories": list(product.categories),
                        }
                    )

                logger.info(
                    f"🔍 [INGREDIENT_MATCHER] Found {len(products)} products for '{ingredient}'"
                )
                return products

        except Exception as e:
            logger.error(f"Error searching products for '{ingredient}': {e}")
            # Fallback to mock if gRPC fails
            return self._mock_search_products(ingredient)

    def _mock_search_products(self, ingredient: str) -> list:
        """Mock product search for fallback when gRPC is not available"""
        # Simple mock mapping - now using exact product names
        mock_products = {
            "Chicken Breast": [
                {
                    "id": "CHICKEN001",
                    "name": "Chicken Breast",
                    "description": "Fresh boneless, skinless chicken breast",
                    "price": "$8.99",
                }
            ],
            "Ground Beef": [
                {
                    "id": "BEEF001",
                    "name": "Ground Beef",
                    "description": "Fresh lean ground beef, 80/20 blend",
                    "price": "$6.99",
                }
            ],
            "Garlic": [
                {
                    "id": "GARLIC001",
                    "name": "Garlic",
                    "description": "Fresh garlic bulbs",
                    "price": "$1.49",
                }
            ],
            "Bell Peppers": [
                {
                    "id": "BELLPEPPER001",
                    "name": "Bell Peppers",
                    "description": "Fresh colorful bell peppers",
                    "price": "$2.49",
                }
            ],
            "Yellow Onion": [
                {
                    "id": "ONION001",
                    "name": "Yellow Onion",
                    "description": "Fresh yellow onions",
                    "price": "$1.99",
                }
            ],
            "Roma Tomatoes": [
                {
                    "id": "TOMATO001",
                    "name": "Roma Tomatoes",
                    "description": "Fresh Roma tomatoes",
                    "price": "$3.49",
                }
            ],
            "Salmon Fillets": [
                {
                    "id": "SALMON001",
                    "name": "Salmon Fillets",
                    "description": "Fresh Atlantic salmon fillets",
                    "price": "$14.99",
                }
            ],
            "Mixed Greens": [
                {
                    "id": "MIXEDGREENS001",
                    "name": "Mixed Greens",
                    "description": "Fresh mixed salad greens",
                    "price": "$4.49",
                }
            ],
            "Avocado": [
                {
                    "id": "AVOCADO001",
                    "name": "Avocado",
                    "description": "Fresh ripe avocados",
                    "price": "$3.99",
                }
            ],
        }

        # Direct match first
        if ingredient in mock_products:
            return mock_products[ingredient]

        # Fallback: case-insensitive partial matching
        for key, products in mock_products.items():
            if key.lower() in ingredient.lower() or ingredient.lower() in key.lower():
                return products

        return []


def get_agent_card(host: str, port: int) -> AgentCard:
    """Create agent card for the ingredient matcher"""
    import os

    skill = AgentSkill(
        id="match_ingredients",
        name="Ingredient Matching",
        description="Matches recipe ingredients to product catalog IDs",
        tags=["ingredients", "products", "matching"],
        examples=[
            "Match these ingredients: tomatoes, onions, garlic",
            "Find product IDs for: chicken, bread, milk",
        ],
    )

    # Use Kubernetes service name if available, otherwise fall back to host:port
    service_name = os.environ.get("SERVICE_NAME", "ingredientmatcheragent")
    service_port = os.environ.get("SERVICE_PORT", "8080")
    agent_url = f"http://{service_name}:{service_port}/"

    print(f"🔍 [INGREDIENT_MATCHER] Agent card URL: {agent_url}")

    return AgentCard(
        name="Ingredient Matcher Agent",
        description="Matches recipe ingredients to product catalog entries using A2A protocol",
        url=agent_url,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(
            input_modes=["text"], output_modes=["text"], streaming=False
        ),
        skills=[skill],
    )


def main():
    """Main function to start the Ingredient Matcher Agent server"""
    host = "0.0.0.0"
    port = 8080

    logger.info(f"Starting Ingredient Matcher Agent on {host}:{port}")

    # Create agent card
    agent_card = get_agent_card(host, port)

    # Create task store
    task_store = InMemoryTaskStore()

    # Create agent executor
    agent_executor = IngredientMatcherExecutor()

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
