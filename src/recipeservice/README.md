# Cart-To-Kitchen AI Assistant

This document describes the Cart-To-Kitchen AI Assistant. The AI powered assistant is a microservice with 2 agents and is fully functional with end-to-end cart integration.

## What It Does

The Cart-To-Kitchen AI Assistant generates dynamic AI recipes based on cart items and suggests additional items to add to cart. This is powered by Google's Gemini and Imagen through the Agent Development Kit (ADK) and Agent to Agent (A2A) protocol. This feature generates personalized recipe suggestions based on users' current cart contents while maintaining full compatibility with the existing A2A architecture.

### RecipeService (Orchestrator)

- **Generates recipe requests** ie. Create recipe for chicken stirfry
- **Extracts ingredients** from natural language recipe descriptions
- **Orchestrates A2A agents** process the recipe step-by-step
- **Returns success confirmation** with matched products and ingredients
- **Generates AI-powered suggested recipes** based on user's current cart contents

### IngredientMatcherAgent (A2A Agent)

- **Receives ingredient lists** from RecipeService via A2A protocol
- **Matches ingredients to product catalog** using the productcatalogservice
- **Returns product IDs** for ingredients found in the store catalog
- **Handles ingredient variations** (e.g., "chicken breast" → "chicken" product)

### CartAdderAgent (A2A Agent)

- **Receives product lists** from RecipeService via A2A protocol
- **Adds items to shopping cart** using real gRPC calls to cartservice
- **Manages cart operations** for recipe-based shopping with actual cart persistence
- **Confirms successful additions** back to RecipeService with cart contents summary

### User Experience Flow

The system supports multiple shopping workflows that seamlessly integrate with AI-powered recipe suggestions:

#### **Workflow 1: Recipe-First Shopping**

1. **📖 User browses recipe collection** on the recipes page
2. **🔍 User selects a recipe** and views detailed ingredients list
3. **☑️ User selects specific ingredients** (or chooses "Select All") from the recipe
4. **🛒 User clicks "Add to Cart"** for selected ingredients
5. **📡 Cart updates instantly** via SSE with new items
6. **🤖 AI detects 2+ cart items** and generates personalized recipe suggestions
7. **💡 User discovers additional recipes** they can make with their current cart
8. **➕ User adds more ingredients** from suggested recipes to expand meal options

#### **Workflow 2: Grocery-First Shopping**

1. **🏪 User shops from main grocery page** browsing product categories
2. **🛒 User adds individual products** to cart (meat, vegetables, pantry items)
3. **📡 Cart updates in real-time** as items are added
4. **🤖 AI detects 2+ cart items** and automatically generates recipe suggestions
5. **🍽️ User sees "What can I make with this?"** personalized recipe recommendations
6. **💭 User discovers recipe possibilities** they hadn't considered
7. **✅ User adds missing ingredients** to complete recipes they want to try

#### **Workflow 3: Hybrid Discovery Shopping**

1. **🛒 User starts with basic grocery shopping** (chicken, rice, vegetables)
2. **🤖 AI suggests recipes** like "Chicken Fried Rice" and "Veggie Stir Fry"
3. **📖 User explores suggested recipe details** to see full ingredient lists
4. **💡 User realizes they're missing key ingredients** (soy sauce, sesame oil, garlic)
5. **➕ User adds missing ingredients** to complete multiple recipe options
6. **🍽️ AI updates suggestions** with new possibilities based on expanded cart
7. **🎯 User ends up with ingredients for 3-4 complete meals** instead of just basic groceries

### Real-Time Updates with Server-Sent Events

The system includes real-time cart synchronization to ensure users always see current cart status:

- **SSE Connection**: Frontend establishes a persistent Server-Sent Events connection (`/cart/updates`)
- **Instant Updates**: When cart items are added via recipe agents, all connected clients receive immediate updates
- **Cart Status Indicators**: Recipe pages show live cart quantities without requiring page refresh
- **Selective Processing**: Users can check/uncheck individual ingredients before adding to cart

## Technology Stack

This project is built using the **Agent Development Kit (ADK)**, which provides the framework for creating and managing agents. Communication between the `RecipeService` and the specialized agents is handled by the **Agent-to-Agent (A2A)** protocol, enabling a flexible and decoupled architecture.

## Architecture

```
+------+   gRPC    +----------------+   A2A    +------------------------+   gRPC    +-----------------------+
| User |---------->|  RecipeService |--------->| IngredientMatcherAgent |---------->| productcatalogservice |
+------+           +----------------+          +------------------------+           +-----------------------+
                         |                              |
                         | A2A                          | gRPC
                         v                              v
                   +----------------+   gRPC    +--------------+
                   | CartAdderAgent |---------->|  cartservice |
                   +----------------+           +--------------+
```
