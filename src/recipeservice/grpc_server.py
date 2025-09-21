# gRPC Server for RecipeService that processes recipes
# This bridges gRPC requests to recipe processing logic

import os
import grpc
from concurrent import futures
import recipe_pb2, recipe_pb2_grpc
from recipe_store import RecipeStore
from multi_tool_agent.recipe_generator import RecipeGenerator


class RecipeServiceImpl(recipe_pb2_grpc.RecipeServiceServicer):
    def __init__(self):
        self.recipe_store = RecipeStore()
        self.recipe_generator = RecipeGenerator()

    def _optimize_and_encode_image(self, image_file_path: str) -> str:
        """
        Memory-efficient image optimization and base64 encoding
        """
        import base64
        from PIL import Image
        import io
        
        try:
            # Memory optimization: compress image before base64 encoding
            with Image.open(image_file_path) as img:
                # Resize to web-optimized size to save memory
                max_size = (384, 384)  # Smaller than 512x512 for better memory efficiency
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                # Convert to RGB if necessary to reduce file size
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                
                # Save to memory buffer with optimization
                buffer = io.BytesIO()
                # Use JPEG with 75% quality for good compression vs quality balance
                img.save(buffer, format='JPEG', quality=75, optimize=True)
                buffer.seek(0)
                
                # Convert optimized image to base64
                image_data_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
                
                # Log compression ratio
                original_size = os.path.getsize(image_file_path)
                compressed_size = len(buffer.getvalue())
                compression_ratio = (1 - compressed_size / original_size) * 100
                print(f"📦 Image compression: {original_size:,} → {compressed_size:,} bytes ({compression_ratio:.1f}% smaller)")
                
                return image_data_b64
                
        except Exception as e:
            print(f"⚠️ Image optimization failed: {e}")
            # Fallback to original method if compression fails
            try:
                with open(image_file_path, "rb") as img_file:
                    return base64.b64encode(img_file.read()).decode("utf-8")
            except Exception as fallback_e:
                print(f"⚠️ Fallback encoding also failed: {fallback_e}")
                return ""

    def AddRecipe(self, request, context):
        """Handle gRPC AddRecipe requests by forwarding to ADK agent"""
        try:
            # For MVP, directly call the process_recipe function
            # instead of going through the ADK agent interface
            from multi_tool_agent.agent import process_recipe

            result = process_recipe(request.recipe_text, request.user_id)

            if result["status"] == "success":
                return recipe_pb2.AddRecipeResponse(
                    success=True,
                    message=result["message"],
                )
            else:
                return recipe_pb2.AddRecipeResponse(
                    success=False,
                    message=result["error_message"],
                )

        except Exception as e:
            return recipe_pb2.AddRecipeResponse(
                success=False, message=f"Error processing recipe: {str(e)}"
            )

    def ListRecipes(self, request, context):
        """Return list of available recipes"""
        try:
            return self.recipe_store.list_recipes()
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error listing recipes: {str(e)}")
            return recipe_pb2.ListRecipesResponse()

    def GetRecipe(self, request, context):
        """Get a specific recipe by ID"""
        try:
            recipe = self.recipe_store.get_recipe(request.recipe_id)
            if recipe:
                return recipe_pb2.GetRecipeResponse(recipe=recipe)
            else:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Recipe {request.recipe_id} not found")
                return recipe_pb2.GetRecipeResponse()
        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error getting recipe: {str(e)}")
            return recipe_pb2.GetRecipeResponse()

    def GetSuggestedRecipes(self, request, context):
        """Generate personalized recipe suggestions based on cart contents"""
        try:
            print(f"Generating suggested recipes for cart items: {list(request.cart_items)}")
            
            # Generate recipes using ADK recipe generator
            suggested_recipes = self.recipe_generator.generate_suggested_recipes(
                cart_items=list(request.cart_items),
                session_id=request.session_id
            )
            
            # Convert to protobuf Recipe objects
            proto_recipes = []
            for recipe_data in suggested_recipes:
                # Convert ingredients to protobuf format
                proto_ingredients = []
                for ing in recipe_data.get("ingredients", []):
                    proto_ingredients.append(recipe_pb2.Ingredient(
                        name=ing["name"],
                        quantity=ing["quantity"],
                        unit=ing["unit"]
                    ))
                
                # Create protobuf Recipe
                # Convert image file path to base64 data for transport
                image_data_b64 = ""
                image_file_path = recipe_data.get("image_data", "")
                print(f"🔍 Recipe '{recipe_data.get('title', 'Unknown')}' image_data: '{image_file_path}'")
                if image_file_path and os.path.exists(image_file_path):
                    try:
                        image_data_b64 = self._optimize_and_encode_image(image_file_path)
                        print(f"📤 Compressed and converted image to base64: {os.path.basename(image_file_path)} ({len(image_data_b64)} chars)")
                    except Exception as e:
                        print(f"⚠️ Failed to encode image {image_file_path}: {e}")
                        image_data_b64 = ""
                else:
                    if image_file_path:
                        print(f"⚠️ Image file does not exist: {image_file_path}")
                    else:
                        print(f"⚠️ No image_data provided for recipe: {recipe_data.get('title', 'Unknown')}")
                
                proto_recipe = recipe_pb2.Recipe(
                    recipe_id=recipe_data["recipe_id"],
                    title=recipe_data["title"],
                    description=recipe_data["description"],
                    default_servings=recipe_data["default_servings"],
                    cook_time=recipe_data["cook_time"],
                    ingredients=proto_ingredients,
                    instructions=recipe_data.get("instructions", []),
                    image_data=image_data_b64  # Base64 encoded image data
                )
                proto_recipes.append(proto_recipe)
            
            print(f"Returning {len(proto_recipes)} suggested recipes")
            return recipe_pb2.ListRecipesResponse(recipes=proto_recipes)
            
        except Exception as e:
            print(f"Error generating suggested recipes: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Error generating suggested recipes: {str(e)}")
            return recipe_pb2.ListRecipesResponse()

    def ProcessRecipeRequest(self, request, context):
        """Process recipe request - supports both natural language and structured recipes"""
        try:
            from multi_tool_agent.agent import process_recipe

            if request.recipe_id:
                # NEW: Structured recipe processing
                recipe = self.recipe_store.get_recipe(request.recipe_id)
                if not recipe:
                    return recipe_pb2.ProcessRecipeResponse(
                        success=False, message=f"Recipe {request.recipe_id} not found"
                    )

                # Scale ingredients for target servings
                target_servings = request.servings or recipe.default_servings
                scaled_ingredients = self.recipe_store.scale_ingredients(
                    recipe, target_servings
                )

                # Convert scaled ingredients to text for existing A2A workflow
                ingredients_text = ", ".join(scaled_ingredients)
                recipe_text = f"Recipe: {recipe.title} (serves {target_servings}). Ingredients: {ingredients_text}"

                result = process_recipe(recipe_text, request.user_id or "default_user")

                if result["status"] == "success":
                    return recipe_pb2.ProcessRecipeResponse(
                        success=True,
                        message=f"Successfully processed {recipe.title} for {target_servings} servings",
                        matched_products=result.get("matched_products", []),
                        ingredients=result.get("ingredients", []),
                        unmatched_ingredients=result.get("unmatched_ingredients", []),
                    )
                else:
                    return recipe_pb2.ProcessRecipeResponse(
                        success=False,
                        message=result.get("error_message", "Unknown error"),
                    )
            else:
                # EXISTING: Natural language processing (unchanged)
                result = process_recipe(
                    request.message, request.user_id or "default_user"
                )

                if result["status"] == "success":
                    return recipe_pb2.ProcessRecipeResponse(
                        success=True,
                        message=result["message"],
                        matched_products=result.get("matched_products", []),
                        ingredients=result.get("ingredients", []),
                        unmatched_ingredients=result.get("unmatched_ingredients", []),
                    )
                else:
                    return recipe_pb2.ProcessRecipeResponse(
                        success=False,
                        message=result.get("error_message", "Unknown error"),
                    )

        except Exception as e:
            return recipe_pb2.ProcessRecipeResponse(
                success=False, message=f"Error processing recipe: {str(e)}"
            )


def serve():
    """Start the gRPC server"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    recipe_pb2_grpc.add_RecipeServiceServicer_to_server(RecipeServiceImpl(), server)
    server.add_insecure_port("[::]:50055")
    server.start()
    print("RecipeService gRPC server started on port 50055")
    print("Forwarding requests to ADK agent...")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
