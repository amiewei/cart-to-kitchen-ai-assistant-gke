"""
Recipe generator using ADK for dynamic recipe generation and Google GenAI Imagen for image generation
"""

import json
import hashlib
import base64
import io
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from cachetools import TTLCache
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import traceback
import threading

try:
    from google.adk.agents import Agent, LlmAgent

    ADK_AVAILABLE = True
    print("✅ ADK is available for recipe generation")
except ImportError:
    # Fallback for development without ADK
    print("⚠️ Warning: ADK not available, using fallback recipe generation")
    ADK_AVAILABLE = False

try:
    import google.genai as genai
    from google.genai import types

    try:
        from PIL import Image as PILImage

        PIL_AVAILABLE = True
    except ImportError:
        PIL_AVAILABLE = False
        print("⚠️ PIL not available, image optimization disabled")

    GENAI_AVAILABLE = True
    print("✅ Google GenAI SDK is available for image generation")
except ImportError:
    print("⚠️ Warning: Google GenAI SDK not available, image generation disabled")
    GENAI_AVAILABLE = False
    PIL_AVAILABLE = False


class RecipeGenerator:
    """Handles dynamic recipe generation using ADK and image generation using Google GenAI Imagen"""

    def __init__(self):
        # Cache recipes for 5 minutes based on cart contents hash
        # Reduced cache size to save memory: 20 recipes × ~50KB = 1MB vs 5MB
        self.cache = TTLCache(maxsize=20, ttl=300)
        # Cache images for 10 minutes (longer since they're more expensive to generate)
        # Reduced cache size to save memory: 10 images × ~500KB = 5MB vs 25MB
        self.image_cache = TTLCache(maxsize=10, ttl=600)
        self.adk_available = ADK_AVAILABLE
        self.genai_available = GENAI_AVAILABLE

        # Initialize Google GenAI for image generation
        if self.genai_available:
            try:
                # Get API key and project from environment
                api_key = os.getenv("GOOGLE_GENAI_API_KEY")
                project = os.getenv(
                    "GOOGLE_CLOUD_PROJECT", "hc-d88e6b33e57d4685ac95888aecb"
                )

                if api_key:
                    # Use Gemini Developer API
                    self.genai_client = genai.Client(api_key=api_key)
                    print("✅ Using Gemini Developer API for image generation")
                else:
                    # Use Vertex AI (default for ADK environments)
                    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-west1")
                    self.genai_client = genai.Client(
                        vertexai=True, project=project, location=location
                    )
                    print(
                        f"✅ Using Vertex AI for image generation: Project={project}, Location={location}"
                    )
                
                print("✅ Google GenAI client initialized for image generation")

            except Exception as e:
                print(f"❌ Failed to initialize Google GenAI: {e}")
                self.genai_client = None
                self.genai_available = False
        else:
            self.genai_client = None

        if self.adk_available:
            # Initialize ADK agent for recipe generation
            try:
                # Use environment variables that should already be available
                use_vertexai = (
                    os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").upper() == "TRUE"
                )
                project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
                location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-west1")

                print(
                    f"🔑 ADK Environment: VertexAI={use_vertexai}, Project={project}, Location={location}"
                )

                self.recipe_agent = Agent(
                    name="recipe_generator",
                    model="gemini-2.5-flash-lite",
                    description="Agent that generates personalized recipes based on cart ingredients",
                    instruction=(
                        "You are a creative chef assistant. Generate recipes using the provided cart ingredients. "
                        "Each recipe should use at least 2-3 cart ingredients and include detailed cooking instructions. "
                        "Return the response as a JSON array of recipe objects with name, description, ingredients, "
                        "instructions, prep_time, and cuisine fields."
                    ),
                )

                print("🍳 Recipe generation agent initialized with ADK")
                print("🖼️ Image generation will use Google GenAI Imagen")
            except Exception as e:
                print(f"❌ Failed to initialize ADK agents: {e}")
                self.recipe_agent = None
                self.adk_available = False
        else:
            self.recipe_agent = None
            print("🔄 Using fallback recipe generation")

    def generate_suggested_recipes(
        self, cart_items: List[str], session_id: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Generates personalized recipe suggestions.
        Returns recipe text immediately and generates images in the background.
        """
        if not cart_items or len(cart_items) < 2:
            print(
                f"📝 Cart has insufficient items ({len(cart_items)}), using fallback recipes"
            )
            return self._get_fallback_recipes(cart_items)

        # Create cache key from cart contents
        cache_key = self._generate_cache_key(cart_items, session_id)

        # Check cache first
        if cache_key in self.cache:
            print(f"🎯 Returning cached recipes for cart: {cart_items[:3]}...")
            recipes = self.cache[cache_key]
            # Even with a cache hit, start background image generation for any missing images
            thread = threading.Thread(target=self.generate_images_for_recipes, args=(recipes, session_id))
            thread.start()
            return recipes

        # If not in cache, generate new recipes
        print(f"Cache miss for {cache_key}. Generating new recipes...")
        try:
            # Generate recipes with ADK agent
            if self.adk_available and self.recipe_agent:
                print(f"🚀 Generating recipes with ADK for cart: {cart_items[:3]}...")
                import asyncio

                recipes = asyncio.run(self._generate_with_adk(cart_items))
            else:
                print(f"⚡ Generating fallback recipes for cart: {cart_items[:3]}...")
                recipes = self._get_fallback_recipes(cart_items)

            # Add generated recipes to cache
            self.cache[cache_key] = recipes
            print(f"✅ Generated and cached {len(recipes)} new recipes.")

            # Start background task to generate images for the new recipes
            thread = threading.Thread(target=self.generate_images_for_recipes, args=(recipes, cart_items, session_id))
            thread.start()

            return recipes
        except Exception as e:
            print(f"Error generating suggested recipes: {e}")
            return self._get_fallback_recipes(cart_items)
            try:
                # Use threading to avoid blocking and add timeout
                import asyncio

                recipes_with_images = asyncio.run(
                    asyncio.wait_for(
                        asyncio.to_thread(self.add_images_to_recipes, recipes),
                        timeout=20.0,  # Max 20 seconds for image generation
                    )
                )
            except asyncio.TimeoutError:
                print(
                    "⏰ Image generation timed out, returning recipes without images for fast response"
                )
                # Return recipes with empty images for very fast response
                for recipe in recipes:
                    recipe["image_data"] = ""
                    recipe["image_url"] = ""
                recipes_with_images = recipes
            except Exception as img_error:
                print(
                    f"⚠️ Image generation failed: {img_error}, using recipes without images"
                )
                for recipe in recipes:
                    recipe["image_data"] = ""
                    recipe["image_url"] = ""
                recipes_with_images = recipes

            # Cache the results (including images)
            self.cache[cache_key] = recipes_with_images
            print(f"✅ Generated {len(recipes_with_images)} recipes successfully")

            return recipes_with_images

        except Exception as e:
            print(f"❌ Error generating recipes: {e}")
            print(f"🔄 Falling back to static recipes")
            return self._get_fallback_recipes(cart_items)

    def _generate_cache_key(self, cart_items: List[str], session_id: str) -> str:
        """Generate a cache key from cart contents and session"""
        # Sort cart items for consistent caching
        sorted_items = sorted(cart_items)
        content = f"{','.join(sorted_items)}:{session_id}"
        return hashlib.md5(content.encode()).hexdigest()

    async def _generate_with_adk(self, cart_items: List[str]) -> List[Dict[str, Any]]:
        """Generate recipes using ADK agent with timeout handling"""
        if not self.adk_available or not self.recipe_agent:
            raise Exception("ADK not available")

        prompt = self._build_prompt(cart_items)

        try:
            import asyncio

            # Add timeout wrapper around your existing ADK logic
            return await asyncio.wait_for(
                self._run_adk_agent(prompt, cart_items),
                timeout=15.0,  # Reduced to 15 seconds for faster response
            )

        except asyncio.TimeoutError:
            print(f"⏰ Recipe generation timed out after 15 seconds")
            print(f"🔄 Falling back to static recipes for faster response")
            return self._get_fallback_recipes(cart_items)
        except Exception as e:
            print(f"❌ Error with ADK agent: {e}")
            import traceback

            print(f"❌ Traceback: {traceback.format_exc()}")
            raise

    async def _run_adk_agent(
        self, prompt: str, cart_items: List[str]
    ) -> List[Dict[str, Any]]:
        """Execute the ADK agent logic - moved from _generate_with_adk"""
        # Use proper ADK Runner pattern like in the documentation
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        print(f"🚀 Starting ADK agent with proper Runner pattern...")

        # Create session service and session
        session_service = InMemorySessionService()
        session = await session_service.create_session(
            app_name="recipe_service",
            user_id="recipe_user",
            session_id="recipe_session",
        )

        # Create runner
        runner = Runner(
            agent=self.recipe_agent,
            app_name="recipe_service",
            session_service=session_service,
        )

        # Create content object for the message
        content = types.Content(role="user", parts=[types.Part(text=prompt)])

        print(f"🔍 Running agent with prompt length: {len(prompt)}")

        # Run the agent and collect events
        events = runner.run(
            user_id="recipe_user", session_id="recipe_session", new_message=content
        )

        # Extract final response from events
        final_response_text = None
        for event in events:
            print(f"🔍 Event type: {type(event)}")
            if hasattr(event, "is_final_response") and event.is_final_response():
                if hasattr(event, "content") and event.content:
                    if hasattr(event.content, "parts") and event.content.parts:
                        final_response_text = event.content.parts[0].text.strip()
                        print(
                            f"🔍 Extracted final response: {final_response_text[:200]}..."
                        )
                        break

        if not final_response_text:
            raise Exception("No final response received from ADK agent")

        print(f"🤖 ADK Response text: {final_response_text[:300]}...")
        print(f"🔍 Full response length: {len(final_response_text)}")
        print(f"🔍 Response starts with: '{final_response_text[:50]}'")

        # Clean up response - remove markdown code blocks if present
        cleaned_response = final_response_text.strip()
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response[7:]  # Remove ```json
        if cleaned_response.startswith("```"):
            cleaned_response = cleaned_response[3:]  # Remove ```
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3]  # Remove ending ```
        cleaned_response = cleaned_response.strip()

        if cleaned_response != final_response_text:
            print(f"🧹 Cleaned response: {cleaned_response[:100]}...")

        # Parse JSON response
        try:
            recipes_data = json.loads(cleaned_response)
        except json.JSONDecodeError as e:
            print(f"❌ JSON parse error: {e}")
            print(f"❌ Failed to parse response: '{cleaned_response}'")
            raise Exception(f"Invalid JSON response from ADK agent: {e}")

        # Validate and format recipes
        recipes = []
        for i, recipe_data in enumerate(recipes_data[:3]):  # Limit to 3 recipes
            recipe = self._format_recipe(recipe_data, i, cart_items)
            if recipe:
                recipes.append(recipe)

        return recipes

    def _build_prompt(self, cart_items: List[str]) -> str:
        """Build the prompt for ADK recipe generation"""
        cart_list = ", ".join(cart_items)

        return f"""
Generate 3 recipes of different cuisines, meal types and cooking methods using cart ingredients: {cart_list}

You are a helpful recipe assistant that can only use the ingredients provided.
- The generated recipe titles, descriptions, and instructions must ONLY refer to items listed in ingredients.
- **DO NOT** substitute ingredients. For example, if the list contains "Bananas", you must use "Bananas" and not "Plantains". 

Ingredients Requirements:
• Use 2+ cart ingredients per recipe (prefer 3-5)
• Use exact cart names: {cart_list}
• Add 1-2 additional ingredients not in the cart. Total ingredients should not exceed 10.
• for each ingredient name, don't add any modifiers or descriptors such as "diced", "chopped", "optional", "sliced" or commas
• Include quantities: "2 cups Roma Tomatoes", "1 lb Ground Beef"
• Don't list basic seasonings (salt, pepper, sugar, cooking oil, water) - assume available
Instructions Requirements:
• Provide 5-8 detailed cooking steps with times/temps. Don't mention the word Step x.
• Do not include any ingredients that are not listed in the ingredient section, except for basic seasonings: Salt, black pepper, water, cooking oil
• For prep time, ensure it adds up correctly based on time durations mentioned in the instructions

Return as JSON array using this exact format:
[{{
  "name": "Descriptive Recipe Name",
  "description": "Detailed 1 sentence description of the dish and its appeal.",
  "ingredients": ["2 cups Roma Tomatoes", "1 pound Ground Beef", "1/2 cup Cheddar Cheese", "2 tablespoons Taco Seasoning"],
  "instructions": [
    "Detailed preparation instruction with specifics",
    "Detailed cooking instruction with time/temp",
    "Continue with specific techniques",
    "Include seasoning and flavor development",
    "Final cooking or assembly steps",
    "Serving suggestions using only listed ingredients"
  ],
  "prep_time": "25 minutes"
}}]


Focus on restaurant-quality dishes maximizing cart ingredients.
"""

    def _format_recipe(
        self, recipe_data: Dict[str, Any], index: int, cart_items: List[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Format recipe data into standard format matching protobuf Recipe schema"""
        try:
            return {
                "recipe_id": f"suggested_{hashlib.md5(recipe_data.get('name', f'recipe_{index}').encode()).hexdigest()[:8]}",
                "title": recipe_data.get("name", f"Suggested Recipe {index + 1}"),
                "description": recipe_data.get(
                    "description", "A delicious recipe made with your cart items"
                ),
                "default_servings": 4,  # Default serving size
                "cook_time": recipe_data.get("prep_time", "20 minutes"),
                "ingredients": self._format_ingredients(
                    recipe_data.get("ingredients", []), cart_items
                ),
                "instructions": recipe_data.get(
                    "instructions", ["Prepare ingredients", "Cook as desired"]
                ),
                "image_data": "",  # Base64 image data (fallback)
                "image_url": "",  # URL to optimized image for fast web display
                # Note: cuisine, difficulty not in protobuf Recipe message
            }
        except Exception as e:
            print(f"❌ Error formatting recipe {index}: {e}")
            return None

    def _format_ingredients(
        self, ingredients: List[str], cart_items: List[str] = None
    ) -> List[Dict[str, Any]]:
        """Format ingredients list into structured format with proper units and quantities"""
        import re

        formatted = []
        for ingredient in ingredients:
            ingredient = ingredient.strip()

            # Define known cooking units to avoid matching ingredient words as units
            known_units = r"(cups?|cup|tablespoons?|tablespoon|tbsp|teaspoons?|teaspoon|tsp|pounds?|pound|lbs?|lb|ounces?|ounce|oz|pieces?|piece|cloves?|clove|slices?|slice|cans?|can|packages?|package|pkg)"

            # Improved pattern that only matches known cooking units
            pattern = rf"^((?:\d+\s+)?\d+(?:\.\d+)?(?:/\d+)?)\s+{known_units}\s+(.+)$"
            match = re.match(pattern, ingredient, re.IGNORECASE)

            quantity = 1.0
            unit = "piece"
            name = ingredient

            if match:
                quantity_str, unit_str, ingredient_name = match.groups()

                # Parse quantity with better fraction handling
                if quantity_str:
                    quantity_str = quantity_str.strip()
                    try:
                        if "/" in quantity_str:
                            # Handle mixed fractions like "1 1/2" or simple fractions like "1/2"
                            parts = quantity_str.split()
                            if len(parts) == 2:  # Mixed number like "1 1/2"
                                whole = float(parts[0])
                                fraction_parts = parts[1].split("/")
                                if len(fraction_parts) == 2:
                                    numerator, denominator = fraction_parts
                                    quantity = whole + (
                                        float(numerator) / float(denominator)
                                    )
                                else:
                                    quantity = whole
                            else:  # Simple fraction like "1/2"
                                fraction_parts = quantity_str.split("/")
                                if len(fraction_parts) == 2:
                                    numerator, denominator = fraction_parts
                                    # Validate that we have valid numbers
                                    if numerator.strip() and denominator.strip():
                                        quantity = float(numerator) / float(denominator)
                                    else:
                                        quantity = (
                                            1.0  # Fallback for malformed fractions
                                        )
                                else:
                                    quantity = 1.0
                        else:
                            quantity = float(quantity_str)
                    except (ValueError, ZeroDivisionError):
                        quantity = 1.0

                # Map unit to standard form
                if unit_str:
                    unit_lower = unit_str.lower()
                    unit_mapping = {
                        "cup": "cup",
                        "cups": "cup",
                        "tablespoon": "tablespoon",
                        "tablespoons": "tablespoon",
                        "tbsp": "tablespoon",
                        "teaspoon": "teaspoon",
                        "teaspoons": "teaspoon",
                        "tsp": "teaspoon",
                        "pound": "pound",
                        "pounds": "pound",
                        "lb": "pound",
                        "lbs": "pound",
                        "ounce": "ounce",
                        "ounces": "ounce",
                        "oz": "ounce",
                        "piece": "piece",
                        "pieces": "piece",
                        "clove": "clove",
                        "cloves": "clove",
                        "slice": "slice",
                        "slices": "slice",
                        "can": "can",
                        "cans": "can",
                        "package": "package",
                        "packages": "package",
                        "pkg": "package",
                    }
                    unit = unit_mapping.get(unit_lower, unit_str)

                # Clean ingredient name - ONLY remove obvious descriptors, preserve main name
                if ingredient_name:
                    name = ingredient_name.strip()

                    # Remove parenthetical content like "(optional)" or "(diced)"
                    name = re.sub(r"\s*\([^)]*\)", "", name)

                    # Remove preparation descriptors at the end
                    name = re.sub(
                        r",\s*(diced|chopped|sliced|minced|optional)$",
                        "",
                        name,
                        flags=re.IGNORECASE,
                    )

                    # Clean up whitespace
                    name = name.strip()
            else:
                # If pattern doesn't match, try simpler approach - just clean the whole string
                # Remove quantity-like prefixes but preserve the full ingredient name
                cleaned = re.sub(r"^(/?[\d\s/]*)\s*", "", ingredient)
                if cleaned:
                    name = cleaned.strip()
                    # Remove any remaining unit words from the beginning
                    name = re.sub(rf"^{known_units}\s+", "", name, flags=re.IGNORECASE)

            # Try to match with exact cart item names
            if cart_items and name:
                best_match = None
                best_match_score = 0

                for cart_item in cart_items:
                    # Calculate match score
                    cart_lower = cart_item.lower()
                    name_lower = name.lower()

                    # Perfect match gets highest score
                    if cart_lower == name_lower:
                        best_match = cart_item
                        best_match_score = 100
                        break

                    # Cart item contained in ingredient name
                    elif cart_lower in name_lower:
                        score = (len(cart_lower) / len(name_lower)) * 80
                        if score > best_match_score:
                            best_match = cart_item
                            best_match_score = score

                    # Ingredient name contained in cart item
                    elif name_lower in cart_lower:
                        score = (len(name_lower) / len(cart_lower)) * 70
                        if score > best_match_score:
                            best_match = cart_item
                            best_match_score = score

                    # Partial word matching for common variations
                    else:
                        # Handle plurals and common variations
                        name_words = name_lower.split()
                        cart_words = cart_lower.split()

                        # Check if any significant words match
                        for name_word in name_words:
                            if len(name_word) > 3:  # Only match significant words
                                for cart_word in cart_words:
                                    if name_word == cart_word or name_word.rstrip(
                                        "s"
                                    ) == cart_word.rstrip("s"):
                                        score = 50
                                        if score > best_match_score:
                                            best_match = cart_item
                                            best_match_score = score

                # Use best match if score is high enough
                if best_match and best_match_score > 40:
                    name = best_match

            # Ensure ingredient name is properly capitalized (title case)
            if name:
                name = name.title()

            formatted.append({"name": name, "quantity": quantity, "unit": unit})

        return formatted

    def _get_fallback_recipes(self, cart_items: List[str]) -> List[Dict[str, Any]]:
        """Return fallback recipes when ADK is unavailable"""
        print(f"🔄 Using fallback recipes for cart: {cart_items}")

        # More detailed fallback recipes that use multiple cart ingredients
        main_ingredients = cart_items[:4] if len(cart_items) >= 4 else cart_items

        fallback_recipes = [
            {
                "recipe_id": "fallback_skillet_medley",
                "title": f"One-Skillet {' & '.join(main_ingredients[:2])} Medley",
                "description": "A hearty one-pan dish that brings together your cart ingredients with simple seasonings for a satisfying meal.",
                "default_servings": 4,
                "cook_time": "25 minutes",
                "image_data": "",  # No images for fallback recipes
                "image_url": "",  # No images for fallback recipes
                "ingredients": [
                    {"name": item.title(), "quantity": 1.0, "unit": "piece"}
                    for item in main_ingredients
                ],
                "instructions": [
                    f"Prepare all ingredients: wash and chop {main_ingredients[0] if main_ingredients else 'vegetables'} into bite-sized pieces",
                    "Heat a large skillet or pan over medium-high heat with a splash of oil",
                    f"Add {main_ingredients[0] if main_ingredients else 'main ingredient'} to the pan and cook for 3-4 minutes until starting to soften",
                    f"Add {', '.join(main_ingredients[1:3]) if len(main_ingredients) > 1 else 'remaining ingredients'} and stir to combine",
                    "Season generously with salt and pepper, then reduce heat to medium",
                    "Cover and cook for 8-10 minutes, stirring occasionally, until all ingredients are tender",
                    "Taste and adjust seasoning, then serve hot as a complete meal",
                ],
            },
            {
                "recipe_id": "fallback_fresh_combination",
                "title": f"Fresh {main_ingredients[0] if main_ingredients else 'Ingredient'} Combination",
                "description": "A light and flavorful dish that showcases your ingredients with minimal cooking for maximum freshness.",
                "default_servings": 3,
                "cook_time": "15 minutes",
                "image_data": "",  # No images for fallback recipes
                "image_url": "",  # No images for fallback recipes
                "ingredients": [
                    {"name": item.title(), "quantity": 1.0, "unit": "piece"}
                    for item in main_ingredients[:3]
                ],
                "instructions": [
                    f"Clean and prepare {main_ingredients[0] if main_ingredients else 'ingredients'} by removing any unwanted parts and cutting into uniform pieces",
                    f"If using {main_ingredients[1] if len(main_ingredients) > 1 else 'second ingredient'}, prepare it by chopping or slicing as appropriate",
                    "Arrange prepared ingredients in a large mixing bowl or serving dish",
                    "Lightly season with salt and pepper to enhance natural flavors",
                    "Gently toss or combine all ingredients, allowing flavors to meld",
                    "Let stand for 5 minutes to allow ingredients to release their natural juices",
                    "Serve immediately as a fresh, healthy dish that highlights each ingredient's unique qualities",
                ],
            },
            {
                "recipe_id": "fallback_roasted_blend",
                "title": f"Roasted {' & '.join(main_ingredients[:2]) if len(main_ingredients) >= 2 else 'Garden'} Blend",
                "description": "Oven-roasted ingredients that develop deep, caramelized flavors through high-heat cooking.",
                "default_servings": 4,
                "cook_time": "30 minutes",
                "image_data": "",  # No images for fallback recipes
                "image_url": "",  # No images for fallback recipes
                "ingredients": [
                    {"name": item.title(), "quantity": 1.0, "unit": "piece"}
                    for item in main_ingredients
                ],
                "instructions": [
                    "Preheat your oven to 425°F (220°C) and line a large baking sheet with parchment paper",
                    f"Cut {', '.join(main_ingredients[:2]) if len(main_ingredients) >= 2 else 'all ingredients'} into similar-sized pieces for even cooking",
                    "Spread prepared ingredients in a single layer on the prepared baking sheet",
                    "Drizzle lightly with oil and season generously with salt and pepper",
                    "Toss everything together to ensure even coating of oil and seasonings",
                    "Roast in preheated oven for 20-25 minutes, stirring once halfway through",
                    "Continue cooking until ingredients are golden brown and tender when pierced with a fork",
                    "Remove from oven and let cool for 2-3 minutes before serving hot",
                ],
            },
        ]

        return fallback_recipes[:3]  # Return up to 3 fallback recipes

    def optimize_image_for_web(
        self, image_path: str, max_size: tuple = (384, 384), quality: int = 75
    ) -> str:
        """
        Optimize image for web delivery - reduce size and compress for low memory usage
        Returns path to optimized image
        """
        try:
            if not PIL_AVAILABLE:
                print("⚠️ PIL not available, returning original image")
                return image_path

            # Open and optimize image
            with PILImage.open(image_path) as img:
                # Convert to RGB if necessary
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")

                # Resize to smaller size for better memory efficiency
                # 384x384 is sufficient for recipe thumbnails and saves significant memory
                if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                    img.thumbnail(max_size, PILImage.Resampling.LANCZOS)
                    print(f"🔽 Resized image from original to {img.size}")

                # Create optimized filename
                path_obj = Path(image_path)
                optimized_path = (
                    path_obj.parent / f"{path_obj.stem}_optimized{path_obj.suffix}"
                )

                # Save with higher compression for memory efficiency
                # Quality 75 provides good balance of size vs quality
                img.save(optimized_path, "JPEG", quality=quality, optimize=True)

                # Check file sizes
                original_size = Path(image_path).stat().st_size
                optimized_size = optimized_path.stat().st_size
                compression_ratio = (1 - optimized_size / original_size) * 100

                print(
                    f"📦 Memory-optimized image: {original_size:,} → {optimized_size:,} bytes ({compression_ratio:.1f}% smaller)"
                )

                return str(optimized_path)

        except Exception as e:
            print(f"❌ Error optimizing image {image_path}: {e}")
            return image_path  # Return original if optimization fails

    def clean_filename(self, name: str) -> str:
        """Create a clean filename from recipe name"""
        safe_name = re.sub(r"[^\w\s-]", "", name).strip()
        return re.sub(r"[-\s]+", "_", safe_name).lower()

    def extract_prompt_from_description(self, image_data: str) -> Optional[str]:
        """Extract and clean the image prompt from the recipe description"""
        try:
            data = json.loads(image_data)
            description = data.get("description", "")

            # Clean up the description for better image generation
            # Focus on visual elements
            prompt = description.replace("\n", " ").strip()

            # Add professional food photography keywords
            enhanced_prompt = f"Professional food photography: {prompt}, high quality, appetizing, well-lit, restaurant quality"

            return enhanced_prompt[:1000]  # Keep within reasonable length
        except:
            return None

    def generate_recipe_image(
        self, recipe_name: str, recipe_description: str, ingredients: List[str]
    ) -> Optional[str]:
        """
        Generate an image for a recipe using Google GenAI Imagen
        Returns image file path or None if generation fails
        """
        if not self.genai_available or not self.genai_client:
            print("🖼️ Google GenAI not available for image generation")
            return None

        # Create cache key for the image
        image_cache_key = hashlib.md5(
            f"{recipe_name}:{recipe_description}".encode()
        ).hexdigest()

        # Check cache first
        if image_cache_key in self.image_cache:
            print(f"🎯 Returning cached image for recipe: {recipe_name}")
            return self.image_cache[image_cache_key]

        try:
            # Create a prompt for image generation
            main_ingredients = [
                ing.get("name", str(ing)) if isinstance(ing, dict) else str(ing)
                for ing in ingredients[:3]
            ]
            ingredient_list = ", ".join(main_ingredients)

            image_prompt = f"""Professional food photography: A beautiful {recipe_name} featuring {ingredient_list}. {recipe_description}. High quality, appetizing, well-lit, restaurant quality plating. The image should look like it could be featured in a cookbook or food magazine. No text or labels in the image. Focus on making the food look delicious and properly plated."""

            print(f"🖼️ Generating image for: {recipe_name}")
            print(f"📝 Using prompt: {image_prompt[:200]}...")

            # Generate image using Google GenAI Imagen
            response = self.genai_client.models.generate_images(
                model="imagen-3.0-fast-generate-001",
                prompt=image_prompt,
                config={
                    "number_of_images": 1,
                    "aspect_ratio": "16:9",
                }
            )

            # Get the generated image
            generated_image = response.generated_images[0]

            # Create output directory
            output_dir = Path("generated_food_images")
            output_dir.mkdir(exist_ok=True)

            # Create filename
            clean_name = self.clean_filename(recipe_name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = output_dir / f"{clean_name}_{timestamp}.jpg"

            # Save the image using image_bytes from Google GenAI SDK
            with open(str(output_file), "wb") as f:
                f.write(generated_image.image.image_bytes)

            # Verify file was created
            if output_file.exists():
                file_size = output_file.stat().st_size
                print(f"✅ Image saved: {output_file.name} ({file_size:,} bytes)")

                # Optimize image for web delivery
                optimized_path = self.optimize_image_for_web(str(output_file))

                # Cache the optimized file path
                self.image_cache[image_cache_key] = optimized_path
                return optimized_path
            else:
                print(f"❌ Failed to save image to {output_file}")
                return None

        except Exception as e:
            print(f"❌ Error generating image for {recipe_name}: {e}")
            print(f"❌ Traceback: {traceback.format_exc()}")
            return None

    def generate_recipe_image_with_description(
        self, recipe_name: str, image_data: str
    ) -> Optional[str]:
        """
        Generate an image for a recipe using existing image description data
        Returns image file path or None if generation fails
        """
        if not self.genai_available or not self.genai_client:
            print("🖼️ Google GenAI not available for image generation")
            return None

        # Create cache key for the image
        image_cache_key = hashlib.md5(
            f"{recipe_name}:{image_data}".encode()
        ).hexdigest()

        # Check cache first
        if image_cache_key in self.image_cache:
            print(f"🎯 Returning cached image for recipe: {recipe_name}")
            return self.image_cache[image_cache_key]

        try:
            # Extract prompt from description
            prompt = self.extract_prompt_from_description(image_data)
            if not prompt:
                print(f"❌ Could not extract prompt from image data for: {recipe_name}")
                return None

            print(f"🖼️ Generating image for: {recipe_name}")
            print(f"📝 Using extracted prompt: {prompt[:200]}...")

            # Generate image using Google GenAI Imagen
            response = self.genai_client.models.generate_images(
                model="imagen-3.0-fast-generate-001",
                prompt=prompt,
                config={
                    "number_of_images": 1,
                    "aspect_ratio": "16:9",
                }
            )

            # Get the generated image
            generated_image = response.generated_images[0]

            # Create output directory
            output_dir = Path("generated_food_images")
            output_dir.mkdir(exist_ok=True)

            # Create filename
            clean_name = self.clean_filename(recipe_name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = output_dir / f"{clean_name}_{timestamp}.jpg"

            # Save the image using image_bytes from Google GenAI SDK
            with open(str(output_file), "wb") as f:
                f.write(generated_image.image.image_bytes)

            # Verify file was created
            if output_file.exists():
                file_size = output_file.stat().st_size
                print(f"✅ Image saved: {output_file.name} ({file_size:,} bytes)")

                # Optimize image for web delivery
                optimized_path = self.optimize_image_for_web(str(output_file))

                # Cache the optimized file path
                self.image_cache[image_cache_key] = optimized_path
                return optimized_path
            else:
                print(f"❌ Failed to save image to {output_file}")
                return None

        except Exception as e:
            print(f"❌ Error generating image for {recipe_name}: {e}")
            print(f"❌ Traceback: {traceback.format_exc()}")
            return None

    def add_images_to_recipes(
        self, recipes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Add generated images to recipes using Google GenAI Imagen
        Returns recipes with image_data field added
        """
        if not self.genai_available or not self.genai_client:
            print("🖼️ Google GenAI not available, returning recipes without images")
            for recipe in recipes:
                recipe["image_data"] = None
            return recipes

        # Phase 1: Use parallel processing for faster image generation
        print(f"🚀 Starting parallel image generation for {len(recipes)} recipes...")
        import asyncio

        # Run parallel image generation
        enhanced_recipes = asyncio.run(self._add_images_to_recipes_parallel(recipes))

        successful_images = len([r for r in enhanced_recipes if r.get("image_data")])
        print(
            f"✅ Successfully generated {successful_images} images using parallel processing"
        )

        # Images are served via base64 encoding in gRPC responses
        if successful_images > 0:
            print(
                f"🚀 Generated {successful_images} images, serving via base64 encoding"
            )

        return enhanced_recipes

    async def _add_images_to_recipes_parallel(
        self, recipes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Parallel version of add_images_to_recipes using asyncio.gather()
        Phase 1: Speed improvement from 18s → 6s by generating all images concurrently
        """
        print(f"⚡ Phase 1: Parallel processing {len(recipes)} images concurrently...")

        # Create tasks for parallel image generation
        tasks = []
        for i, recipe in enumerate(recipes):
            task = self._generate_single_recipe_image_async(recipe, i)
            tasks.append(task)

        # Execute all image generation tasks in parallel
        start_time = asyncio.get_event_loop().time()
        enhanced_recipes = await asyncio.gather(*tasks, return_exceptions=True)
        end_time = asyncio.get_event_loop().time()

        # Handle any exceptions and ensure we return valid recipes
        valid_recipes = []
        for i, result in enumerate(enhanced_recipes):
            if isinstance(result, Exception):
                print(f"❌ Error generating image for recipe {i}: {result}")
                # Create a fallback recipe
                recipe_copy = recipes[i].copy()
                recipe_copy["image_data"] = None
                recipe_copy["image_url"] = ""
                valid_recipes.append(recipe_copy)
            else:
                valid_recipes.append(result)

        total_time = end_time - start_time
        print(
            f"⚡ Parallel image generation completed in {total_time:.2f}s (vs ~18s sequential)"
        )

        return valid_recipes

    async def _generate_single_recipe_image_async(
        self, recipe: Dict[str, Any], recipe_index: int
    ) -> Dict[str, Any]:
        """
        Async wrapper for individual recipe image generation
        Maintains existing caching and file-based approach for Phase 1 safety
        """
        recipe_copy = recipe.copy()
        recipe_title = recipe.get("title", f"Unknown Recipe {recipe_index}")

        try:
            print(f"🎨 [{recipe_index}] Generating image for: {recipe_title}")

            # First check if the recipe already has image_data (descriptions from ADK)
            existing_image_data = recipe.get("image_data")

            # Run image generation in thread pool to avoid blocking
            if existing_image_data:
                # Use existing description to generate actual image
                image_file_path = await asyncio.to_thread(
                    self.generate_recipe_image_with_description,
                    recipe_title,
                    existing_image_data,
                )
            else:
                # Generate image directly from recipe data
                image_file_path = await asyncio.to_thread(
                    self.generate_recipe_image,
                    recipe_title,
                    recipe.get("description", ""),
                    recipe.get("ingredients", []),
                )

            if image_file_path:
                # For low latency web display, use optimized image URL
                # image_file_path already includes _optimized.jpg suffix
                if image_file_path.endswith("_optimized.jpg"):
                    optimized_path = image_file_path
                else:
                    optimized_path = image_file_path.replace(".jpg", "_optimized.jpg")

                image_filename = Path(optimized_path).name

                # In development, use static file serving
                # In production, images are embedded as base64 in gRPC response
                is_development = (
                    os.getenv("ENVIRONMENT") == "development" or 
                    os.getenv("ENV_PLATFORM") == "local" or
                    os.path.exists(os.path.expanduser("~/Projects"))
                )
                
                if is_development:  # Development
                    recipe_copy["image_url"] = (
                        f"/static/images/recipes/{image_filename}"
                    )
                else:  # Production
                    recipe_copy["image_url"] = ""  # Use base64 instead

                # Always keep base64 for gRPC responses
                recipe_copy["image_data"] = image_file_path
                print(f"✅ [{recipe_index}] Added image to recipe: {recipe_title}")
            else:
                recipe_copy["image_data"] = None
                recipe_copy["image_url"] = ""
                print(
                    f"❌ [{recipe_index}] Failed to generate image for: {recipe_title}"
                )

        except Exception as e:
            print(
                f"❌ [{recipe_index}] Error adding image to recipe {recipe_title}: {e}"
            )
            print(f"❌ Traceback: {traceback.format_exc()}")
            recipe_copy["image_data"] = None
            recipe_copy["image_url"] = ""

        return recipe_copy

    def generate_images_for_recipes(self, recipes: List[Dict[str, Any]], cart_items: List[str], session_id: str):
        """
        Generates images for a list of recipes in a background thread.
        This method is designed to be called from a separate thread to avoid blocking.
        Updates the cache with the enhanced recipes containing images.
        """
        if not self.genai_available:
            print("Image generation is not available.")
            return

        print(f"Background task: Starting image generation for {len(recipes)} recipes with cart {cart_items[:3]}...")
        
        # Use the exact same cache key logic from generate_suggested_recipes
        cache_key = self._generate_cache_key(cart_items, session_id)
        print(f"Background task: Using cache key {cache_key}")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            enhanced_recipes = loop.run_until_complete(self._add_images_to_recipes_parallel(recipes))
            
            # Update the cache with the enhanced recipes that now have images
            if enhanced_recipes:
                self.cache[cache_key] = enhanced_recipes
                print(f"🔄 Updated cache with {len(enhanced_recipes)} recipes containing images for key {cache_key}")
                
                # Debug: Check if recipes actually have images
                images_found = sum(1 for recipe in enhanced_recipes if recipe.get("image_data"))
                print(f"🖼️ Cache update: {images_found}/{len(enhanced_recipes)} recipes have image data")
                
        finally:
            loop.close()

        print("Background task: Image generation complete.")
