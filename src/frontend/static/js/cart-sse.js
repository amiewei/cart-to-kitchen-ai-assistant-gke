// Real-time cart updates using Server-Sent Events

class CartSSE {
  constructor() {
    this.eventSource = null;
    this.isConnected = false;
    this.suggestionsTimer = null;
    this.attemptedIngredients = new Set(); // Track ingredients that were attempted to be added
  }

  connect() {
    if (this.eventSource) {
      this.eventSource.close();
    }

    this.eventSource = new EventSource("/cart/updates");

    this.eventSource.onmessage = (event) => {
      try {
        const cartData = JSON.parse(event.data);
        console.log("Received SSE cart data:", cartData);
        this.updateCartDisplay(cartData);
      } catch (error) {
        console.warn("Failed to parse cart update:", error);
        console.warn("Raw event data:", event.data);
      }
    };

    this.eventSource.onerror = (error) => {
      console.warn("SSE connection error:", error);
      // Reconnect after a delay
      setTimeout(() => this.connect(), 5000);
    };
  }

  updateCartDisplay(cartData) {
    // Handle different possible field names for cart count
    const cartCount =
      cartData.count || cartData.cart_items_count || cartData.size || 0;
    console.log(
      "Updating cart display with count:",
      cartCount,
      "from data:",
      cartData
    );

    // Update cart count in header
    const cartCountElements = document.querySelectorAll(
      ".cart-count, .cart-size, .cart-size-circle"
    );
    cartCountElements.forEach((element) => {
      element.textContent = cartCount;
      // Make sure the element is visible if there are items
      if (cartCount > 0) {
        element.style.display = "";
        if (element.parentElement) {
          element.parentElement.style.display = "";
        }
      }
    });

    // Update ingredient cart status indicators
    if (cartData.items && Array.isArray(cartData.items)) {
      this.updateIngredientStatus(cartData.items);

      // NEW: Refresh suggested recipes when cart changes meaningfully
      this.refreshSuggestedRecipes(cartData.items);
    }
  }

  updateIngredientStatus(cartItems) {
    // Create a map of product names to quantities for matching
    const cartProductMap = {};
    cartItems.forEach((item) => {
      if (item.productName) {
        cartProductMap[item.productName.toLowerCase()] = item.quantity;
      }
    });

    console.log("Cart product map for ingredient matching:", cartProductMap);

    // Update each ingredient's cart status
    document
      .querySelectorAll("[data-ingredient]")
      .forEach((ingredientElement) => {
        const ingredientName = ingredientElement.dataset.ingredient;
        const statusElement = ingredientElement.querySelector(".cart-status");

        if (statusElement && ingredientName) {
          // Check if this ingredient is marked as "not available" from the server
          const isNotAvailable =
            statusElement.innerHTML.includes("Not available");

          // Don't override "Not available" status - preserve it
          if (isNotAvailable) {
            return; // Keep the existing "Not available" status
          }

          // Check if this ingredient matches any cart product by name similarity
          const matchedQuantity = this.findMatchingCartQuantity(
            ingredientName,
            cartProductMap
          );

          if (matchedQuantity > 0) {
            statusElement.innerHTML = `<i class="fas fa-shopping-cart"></i> In cart (${matchedQuantity})`;
            statusElement.className = "text-success cart-status";
            // Don't automatically remove attempted ingredients on page load
            // Only remove them when explicitly adding to cart via user action
          } else {
            // Check if this ingredient was explicitly attempted to be added
            const wasAttemptedToAdd = this.attemptedIngredients.has(
              ingredientName.toLowerCase()
            );

            if (wasAttemptedToAdd) {
              statusElement.innerHTML = `<i class="fas fa-exclamation-triangle"></i> Out of stock`;
              statusElement.className = "text-warning cart-status";
            } else {
              statusElement.innerHTML = "";
              statusElement.className = "cart-status";
            }
          }
        }
      });
  }

  findMatchingCartQuantity(ingredientName, cartProductMap) {
    const lowerIngredient = ingredientName.toLowerCase();

    // Try exact match first
    if (cartProductMap[lowerIngredient]) {
      return cartProductMap[lowerIngredient];
    }

    // Try partial matching - check if ingredient name is contained in any product name
    for (const [productName, quantity] of Object.entries(cartProductMap)) {
      const lowerProductName = productName.toLowerCase();

      // Check both directions: ingredient contains product name or product name contains ingredient
      if (
        lowerIngredient.includes(lowerProductName) ||
        lowerProductName.includes(lowerIngredient)
      ) {
        return quantity;
      }

      // Also check individual words for better matching
      const ingredientWords = lowerIngredient.split(" ");
      const productWords = lowerProductName.split(" ");

      // If any significant word matches (longer than 3 characters)
      for (const ingredientWord of ingredientWords) {
        if (ingredientWord.length > 3) {
          for (const productWord of productWords) {
            if (
              productWord.length > 3 &&
              (ingredientWord.includes(productWord) ||
                productWord.includes(ingredientWord))
            ) {
              return quantity;
            }
          }
        }
      }
    }

    return 0;
  }

  // Method to track when ingredients are attempted to be added
  trackAttemptedIngredient(ingredientName) {
    if (ingredientName) {
      console.log("DEBUG: trackAttemptedIngredient called with:", ingredientName);
      this.attemptedIngredients.add(ingredientName.toLowerCase());
      console.log("Tracked attempted ingredient:", ingredientName);
      console.log("Current attempted ingredients:", Array.from(this.attemptedIngredients));
    }
  }

  // Method to remove an ingredient from attempted list (when successfully added)
  removeAttemptedIngredient(ingredientName) {
    if (ingredientName) {
      this.attemptedIngredients.delete(ingredientName.toLowerCase());
      // Removed console.log to reduce noise
    }
  }

  // Method to clear attempted ingredients (e.g., on successful cart updates)
  clearAttemptedIngredients() {
    this.attemptedIngredients.clear();
    console.log("Cleared all attempted ingredients");
  }

  refreshSuggestedRecipes(cartItems) {
    // Only refresh if we're on the recipes page and have meaningful cart
    if (!document.getElementById("suggested-recipes")) {
      return;
    }

    // Clear any existing debounce timer
    if (this.suggestionsTimer) {
      clearTimeout(this.suggestionsTimer);
    }

    // Debounce suggestions refresh (wait 2 seconds after cart stops changing)
    this.suggestionsTimer = setTimeout(() => {
      this.fetchSuggestedRecipes(cartItems);
    }, 2000);
  }

  async fetchSuggestedRecipes(cartItems) {
    // Store cart items for polling
    this.lastCartItems = cartItems.map(item => item.productName || "unknown-ingredient");
    
    const suggestedSection = document.getElementById("suggested-recipes");
    const loadingState = document.getElementById("suggestions-loading");
    const emptyMessage = document.getElementById("empty-cart-message");
    const recipeGrid = document.getElementById("suggested-recipe-grid");
    const recipeGridContainer = document.getElementById(
      "suggested-recipe-grid-container"
    );

    if (!suggestedSection) return;

    try {
      // Show appropriate state based on cart contents
      if (!cartItems || cartItems.length < 2) {
        // Show empty cart message
        loadingState.style.display = "none";
        emptyMessage.style.display = "block";
        if (recipeGridContainer) recipeGridContainer.style.display = "none";
        return;
      }

      // Show loading state
      loadingState.style.display = "block";
      emptyMessage.style.display = "none";
      if (recipeGridContainer) recipeGridContainer.style.display = "none";

      // Extract ingredient names from cart items
      const ingredientNames = cartItems.map(
        (item) => item.productName || "unknown-ingredient"
      );

      console.log("Fetching suggested recipes for:", ingredientNames);

      // Fetch suggested recipes
      const response = await fetch("/suggested-recipes", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          cart_items: ingredientNames,
          session_id: this.getSessionId(),
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const suggestedRecipes = await response.json();

      // Hide loading state
      loadingState.style.display = "none";

      // Display suggested recipes
      this.displaySuggestedRecipes(suggestedRecipes);
    } catch (error) {
      console.error("Error fetching suggested recipes:", error);

      // Hide loading state and show error message
      loadingState.style.display = "none";
      const recipeGridContainer = document.getElementById(
        "suggested-recipe-grid-container"
      );
      if (recipeGridContainer) {
        recipeGridContainer.style.display = "block";
        recipeGrid.innerHTML = `
                    <div class="col-12 text-center">
                        <p class="text-muted">Unable to generate recipe suggestions at this time.</p>
                    </div>
                `;
      }
    }
  }

  displaySuggestedRecipes(recipes) {
    const recipeGrid = document.getElementById("suggested-recipe-grid");
    const recipeGridContainer = document.getElementById(
      "suggested-recipe-grid-container"
    );
    const template = document.getElementById("suggested-recipe-card-template");

    if (!recipeGrid || !template || !recipes) {
      if (recipeGridContainer) recipeGridContainer.style.display = "none";
      return;
    }

    // Show the container and clear existing recipes
    if (recipeGridContainer) recipeGridContainer.style.display = "block";
    recipeGrid.innerHTML = "";

    // If no recipes, show a message
    if (recipes.length === 0) {
      recipeGrid.innerHTML = `<p class="text-muted text-center">No suggestions found. Try adding more items to your cart!</p>`;
      return;
    }

    // Create recipe cards from template
    recipes.forEach((recipe) => {
      const card = template.content.cloneNode(true);
      const cardElement = card.querySelector(".recipe-card");
      
      // Set a unique ID for each card for easy updates
      cardElement.id = `recipe-${recipe.recipe_id}`;

      // Set up click handler for the entire card
      cardElement.onclick = () =>
        (window.location.href = `/suggested-recipe/${recipe.recipe_id || ""}`);

      // Populate text content first
      card.querySelector(".recipe-title-link").textContent = recipe.title || "Suggested Recipe";
      card.querySelector(".recipe-description").textContent = recipe.description || "A delicious recipe made with your cart items.";
      card.querySelector(".recipe-cook-time").textContent = `⏱️ ${recipe.cook_time || "20 min"}`;
      card.querySelector(".recipe-servings").textContent = `👥 ${recipe.default_servings || 4} servings`;

      const imageContainer = card.querySelector(".recipe-image-container");
      const loadingOverlay = card.querySelector(".loading-overlay");

      // Handle image loading
      if (recipe.image_data) {
        // If image data is already present, display it
        const img = document.createElement("img");
        img.src = `data:image/jpeg;base64,${recipe.image_data}`;
        img.alt = recipe.title;
        imageContainer.appendChild(img);
        loadingOverlay.style.display = "none";
      } else {
        // If no image data, show loading overlay
        loadingOverlay.style.display = "flex";
      }

      recipeGrid.appendChild(card);
    });

    console.log(`Displayed ${recipes.length} suggested recipes with placeholders.`);
    
    // Start polling for images for recipes that don't have them
    this.startProgressiveImagePolling(recipes);
  }

  async addSuggestedRecipeToCart(recipe) {
    const button = document.querySelector(
      `[data-recipe-id="${recipe.recipe_id}"]`
    );

    if (!button) return;

    // Track all ingredients in this recipe as attempted
    if (recipe.ingredients && Array.isArray(recipe.ingredients)) {
      recipe.ingredients.forEach((ingredient) => {
        // Extract ingredient name from structured ingredient object or string
        let ingredientName;
        if (typeof ingredient === "object" && ingredient.name) {
          ingredientName = ingredient.name;
        } else if (typeof ingredient === "string") {
          // Parse ingredient string like "2 cups Roma Tomatoes" to extract "Roma Tomatoes"
          const match = ingredient.match(
            /^(?:\d+(?:\.\d+)?\s+(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|pounds?|pound|lbs?|lb|ounces?|ounce|oz|pieces?|piece|cloves?|clove|slices?|slice|cans?|can|packages?|package|pkg)\s+)?(.+)$/i
          );
          ingredientName = match ? match[1].trim() : ingredient.trim();
        } else {
          ingredientName = String(ingredient).trim();
        }

        if (ingredientName) {
          this.trackAttemptedIngredient(ingredientName);
        }
      });
    }

    // Show loading state
    const originalText = button.textContent;
    button.textContent = "Adding...";
    button.disabled = true;

    try {
      // Use the same endpoint as the standard recipe addition
      const response = await fetch(`/recipe/${recipe.recipe_id}/add-to-cart`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: `servings=${recipe.default_servings || 4}`,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      // Check if response is a redirect (which is the normal flow)
      if (response.redirected || response.status === 302) {
        // Show success state
        button.textContent = "✓ Added!";
        button.classList.add("btn-success");
        button.classList.remove("btn-primary");

        // Reset button after delay
        setTimeout(() => {
          button.textContent = originalText;
          button.disabled = false;
          button.classList.remove("btn-success");
          button.classList.add("btn-primary");
        }, 2000);

        console.log("Successfully added suggested recipe to cart");
        return;
      }

      // For non-redirect responses, try to parse as JSON
      const result = await response.json();

      // Show success state
      button.textContent = "✓ Added!";
      button.classList.add("btn-success");
      button.classList.remove("btn-primary");

      // Reset button after delay
      setTimeout(() => {
        button.textContent = originalText;
        button.disabled = false;
        button.classList.remove("btn-success");
        button.classList.add("btn-primary");
      }, 2000);

      console.log("Successfully added suggested recipe to cart:", result);
    } catch (error) {
      console.error("Error adding suggested recipe to cart:", error);

      // Show error state
      button.textContent = "Error";
      button.classList.add("btn-danger");
      button.classList.remove("btn-primary");

      // Reset button after delay
      setTimeout(() => {
        button.textContent = originalText;
        button.disabled = false;
        button.classList.remove("btn-danger");
        button.classList.add("btn-primary");
      }, 2000);
    }
  }

  getSessionId() {
    // Simple session ID generation - could be enhanced
    let sessionId = localStorage.getItem("recipe-session-id");
    if (!sessionId) {
      sessionId = "session_" + Math.random().toString(36).substr(2, 9);
      localStorage.setItem("recipe-session-id", sessionId);
    }
    return sessionId;
  }

  disconnect() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  startProgressiveImagePolling(initialRecipes) {
    let recipesToPoll = initialRecipes.filter(r => !r.image_data);
    if (recipesToPoll.length === 0) {
      return; // All images are already loaded
    }

    const pollingInterval = 3000; // Poll every 3 seconds
    const maxAttempts = 10;
    let attempts = 0;

    const poll = async () => {
      if (attempts >= maxAttempts || recipesToPoll.length === 0) {
        console.log("Stopping image polling.");
        // On final attempt, hide any remaining loading overlays
        recipesToPoll.forEach(recipe => {
            const card = document.getElementById(`recipe-${recipe.recipe_id}`);
            if (card) {
                const loadingOverlay = card.querySelector(".loading-overlay");
                if (loadingOverlay) loadingOverlay.style.display = "none";
            }
        });
        return;
      }

      attempts++;
      console.log(`Image polling attempt #${attempts} for ${recipesToPoll.length} recipes.`);

      try {
        const response = await fetch("/suggested-recipes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cart_items: this.lastCartItems,
            session_id: this.getSessionId(),
          }),
        });

        if (!response.ok) throw new Error(`Polling failed with status ${response.status}`);

        const updatedRecipes = await response.json();
        
        // Create a map for quick lookup
        const updatedRecipeMap = new Map(updatedRecipes.map(r => [r.recipe_id, r]));

        // Update recipes that now have images
        const stillMissing = [];
        recipesToPoll.forEach(recipe => {
          const updatedRecipe = updatedRecipeMap.get(recipe.recipe_id);
          if (updatedRecipe && updatedRecipe.image_data) {
            console.log(`🖼️ Image found for: ${updatedRecipe.title}`);
            const card = document.getElementById(`recipe-${updatedRecipe.recipe_id}`);
            if (card) {
              const imageContainer = card.querySelector(".recipe-image-container");
              const loadingOverlay = card.querySelector(".loading-overlay");

              // Prevent flashing: only add image if it's not already there
              if (imageContainer && !imageContainer.querySelector("img")) {
                // Create and append the image
                const img = document.createElement("img");
                img.src = `data:image/jpeg;base64,${updatedRecipe.image_data}`;
                img.alt = updatedRecipe.title;
                
                // Hide overlay and add image
                if (loadingOverlay) loadingOverlay.style.display = "none";
                imageContainer.appendChild(img);
              } else {
                console.log(`Image for ${updatedRecipe.title} already exists. Skipping.`);
              }
            }
          } else {
            stillMissing.push(recipe);
          }
        });

        recipesToPoll = stillMissing;

        if (recipesToPoll.length > 0) {
          setTimeout(poll, pollingInterval);
        } else {
            console.log("All recipe images loaded successfully.");
        }

      } catch (error) {
        console.error("Error during image polling:", error);
        setTimeout(poll, pollingInterval * 2); // Wait longer on error
      }
    };

    setTimeout(poll, pollingInterval);
  }

  async fetchSuggestedRecipes(cartItems) {
    // Store cart items for polling
    this.lastCartItems = cartItems.map(item => item.productName || "unknown-ingredient");

    const suggestedSection = document.getElementById("suggested-recipes");
    const loadingState = document.getElementById("suggestions-loading");
    const emptyMessage = document.getElementById("empty-cart-message");
    const recipeGrid = document.getElementById("suggested-recipe-grid");
    const recipeGridContainer = document.getElementById(
      "suggested-recipe-grid-container"
    );

    if (!suggestedSection) return;

    try {
      // Show appropriate state based on cart contents
      if (!cartItems || cartItems.length < 2) {
        // Show empty cart message
        loadingState.style.display = "none";
        emptyMessage.style.display = "block";
        if (recipeGridContainer) recipeGridContainer.style.display = "none";
        return;
      }

      // Show loading state
      loadingState.style.display = "block";
      emptyMessage.style.display = "none";
      if (recipeGridContainer) recipeGridContainer.style.display = "none";

      // Extract ingredient names from cart items
      const ingredientNames = cartItems.map(
        (item) => item.productName || "unknown-ingredient"
      );

      console.log("Fetching suggested recipes for:", ingredientNames);

      // Fetch suggested recipes
      const response = await fetch("/suggested-recipes", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          cart_items: ingredientNames,
          session_id: this.getSessionId(),
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const suggestedRecipes = await response.json();

      // Hide loading state
      loadingState.style.display = "none";

      // Display suggested recipes
      this.displaySuggestedRecipes(suggestedRecipes);
    } catch (error) {
      console.error("Error fetching suggested recipes:", error);

      // Hide loading state and show error message
      loadingState.style.display = "none";
      const recipeGridContainer = document.getElementById(
        "suggested-recipe-grid-container"
      );
      if (recipeGridContainer) {
        recipeGridContainer.style.display = "block";
        recipeGrid.innerHTML = `
                    <div class="col-12 text-center">
                        <p class="text-muted">Unable to generate recipe suggestions at this time.</p>
                    </div>
                `;
      }
    }
  }
}

// Initialize cart SSE when page loads
document.addEventListener("DOMContentLoaded", () => {
  window.cartSSE = new CartSSE();
  window.cartSSE.connect(); // Add the missing connect call
});

// Clean up on page unload
window.addEventListener("beforeunload", () => {
  if (window.cartSSE) {
    window.cartSSE.disconnect();
  }
});
