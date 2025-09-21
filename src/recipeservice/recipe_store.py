import json
import os
from typing import List, Dict, Optional
import recipe_pb2


class RecipeStore:
    """Simple JSON-based recipe datastore"""

    def __init__(self, data_file: str = "data/recipes.json"):
        self.data_file = data_file
        self.recipes = self._load_recipes()

    def _load_recipes(self) -> List[Dict]:
        """Load recipes from JSON file"""
        try:
            with open(self.data_file, "r") as f:
                data = json.load(f)
                return data.get("recipes", [])
        except FileNotFoundError:
            print(f"Recipe data file {self.data_file} not found")
            return []
        except json.JSONDecodeError:
            print(f"Invalid JSON in {self.data_file}")
            return []

    def list_recipes(self) -> recipe_pb2.ListRecipesResponse:
        """Return all recipes as gRPC response"""
        recipes = []
        for recipe_data in self.recipes:
            # Convert ingredients to protobuf format
            ingredients = []
            for ing in recipe_data.get("ingredients", []):
                ingredient = recipe_pb2.Ingredient(
                    name=ing["name"], quantity=float(ing["quantity"]), unit=ing["unit"]
                )
                ingredients.append(ingredient)

            # Create recipe protobuf message
            recipe = recipe_pb2.Recipe(
                recipe_id=recipe_data["recipe_id"],
                title=recipe_data["title"],
                description=recipe_data.get("description", ""),
                default_servings=recipe_data["default_servings"],
                cook_time=recipe_data.get("cook_time", ""),
                ingredients=ingredients,
                instructions=recipe_data.get("instructions", []),  # Add instructions support
            )
            recipes.append(recipe)

        return recipe_pb2.ListRecipesResponse(recipes=recipes)

    def get_recipe(self, recipe_id: str) -> Optional[recipe_pb2.Recipe]:
        """Get a specific recipe by ID"""
        for recipe_data in self.recipes:
            if recipe_data["recipe_id"] == recipe_id:
                # Convert ingredients to protobuf format
                ingredients = []
                for ing in recipe_data.get("ingredients", []):
                    ingredient = recipe_pb2.Ingredient(
                        name=ing["name"],
                        quantity=float(ing["quantity"]),
                        unit=ing["unit"],
                    )
                    ingredients.append(ingredient)

                return recipe_pb2.Recipe(
                    recipe_id=recipe_data["recipe_id"],
                    title=recipe_data["title"],
                    description=recipe_data.get("description", ""),
                    default_servings=recipe_data["default_servings"],
                    cook_time=recipe_data.get("cook_time", ""),
                    ingredients=ingredients,
                    instructions=recipe_data.get("instructions", []),  # Add instructions support
                )
        return None

    def scale_ingredients(
        self, recipe: recipe_pb2.Recipe, target_servings: int
    ) -> List[str]:
        """Scale recipe ingredients for target servings and return as text list"""
        if target_servings <= 0:
            target_servings = recipe.default_servings

        scale_factor = target_servings / recipe.default_servings
        scaled_ingredients = []

        for ingredient in recipe.ingredients:
            scaled_quantity = ingredient.quantity * scale_factor
            # Format the scaled ingredient as text for the existing A2A workflow
            if ingredient.unit in ["pieces", "cloves", "packet"]:
                # For countable items, round to nearest integer
                scaled_text = (
                    f"{round(scaled_quantity)} {ingredient.unit} {ingredient.name}"
                )
            else:
                # For measurable items, keep decimal if needed
                if scaled_quantity == int(scaled_quantity):
                    scaled_text = (
                        f"{int(scaled_quantity)} {ingredient.unit} {ingredient.name}"
                    )
                else:
                    scaled_text = (
                        f"{scaled_quantity:.1f} {ingredient.unit} {ingredient.name}"
                    )

            scaled_ingredients.append(scaled_text)

        return scaled_ingredients
