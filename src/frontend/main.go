// Copyright 2018 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"

	"cloud.google.com/go/profiler"
	"github.com/gorilla/mux"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"google.golang.org/grpc"

	pb "github.com/GoogleCloudPlatform/microservices-demo/src/frontend/genproto"
)

// CachedRecipe represents a suggested recipe stored in the cache
type CachedRecipe struct {
	RecipeId        string              `json:"recipe_id"`
	Title           string              `json:"title"`
	Description     string              `json:"description"`
	CookTime        string              `json:"cook_time"`
	DefaultServings int32               `json:"default_servings"`
	Ingredients     []*CachedIngredient `json:"ingredients"`
	Instructions    []string            `json:"instructions"`
	SessionID       string              `json:"session_id"`
	CreatedAt       time.Time           `json:"created_at"`
	ImageData       string              `json:"image_data,omitempty"` // Base64 encoded image data
}

// CachedIngredient represents an ingredient in a cached recipe
type CachedIngredient struct {
	Name     string  `json:"name"`
	Quantity float32 `json:"quantity"`
	Unit     string  `json:"unit"`
}

const (
	port            = "8080"
	defaultCurrency = "USD"
	cookieMaxAge    = 60 * 60 * 48

	cookiePrefix    = "shop_"
	cookieSessionID = cookiePrefix + "session-id"
	cookieCurrency  = cookiePrefix + "currency"
)

var (
	whitelistedCurrencies = map[string]bool{
		"USD": true,
		"EUR": true,
		"CAD": true,
		"JPY": true,
		"GBP": true,
		"TRY": true,
	}

	baseUrl = ""
)

type ctxKeySessionID struct{}

type CartUpdate struct {
	Count int        `json:"cart_items_count"`
	Items []CartItem `json:"items"`
}

type CartItem struct {
	ProductID   string `json:"productId"`
	ProductName string `json:"productName"`
	Quantity    int32  `json:"quantity"`
}

type frontendServer struct {
	productCatalogSvcAddr string
	productCatalogSvcConn *grpc.ClientConn

	currencySvcAddr string
	currencySvcConn *grpc.ClientConn

	cartSvcAddr string
	cartSvcConn *grpc.ClientConn

	recommendationSvcAddr string
	recommendationSvcConn *grpc.ClientConn

	checkoutSvcAddr string
	checkoutSvcConn *grpc.ClientConn

	shippingSvcAddr string
	shippingSvcConn *grpc.ClientConn

	adSvcAddr string
	adSvcConn *grpc.ClientConn

	recipeSvcAddr string
	recipeSvcConn *grpc.ClientConn

	collectorAddr string
	collectorConn *grpc.ClientConn

	shoppingAssistantSvcAddr string

	// SSE client tracking for real-time cart updates
	cartUpdateClients sync.Map // userID -> chan CartUpdate

	// Cache for suggested recipes by session
	suggestedRecipesCache sync.Map // sessionID -> []Recipe
}

// SSE Methods for cart updates
func (fe *frontendServer) cartUpdatesHandler(w http.ResponseWriter, r *http.Request) {
	sessionID := sessionID(r)
	userID := sessionID // Use sessionID as userID for cart updates

	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.WithField("user_id", userID).Info("Creating new session for path: /cart/updates")

	// Set SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// Create a channel for this client
	clientChan := make(chan CartUpdate, 10)

	// Store the client channel
	fe.cartUpdateClients.Store(userID, clientChan)

	// Clean up when client disconnects
	defer func() {
		fe.cartUpdateClients.Delete(userID)
		close(clientChan)
	}()

	// Keep connection alive and send updates
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	// Send initial cart data with items
	if cart, err := fe.getCart(r.Context(), userID); err == nil {
		cartItemsCount := cartSize(cart)

		// Convert cart items to serializable format (same as notifyCartUpdate)
		cartItems := make([]CartItem, len(cart))
		for i, item := range cart {
			productName := fe.getProductName(item.ProductId)
			cartItems[i] = CartItem{
				ProductID:   item.ProductId,
				ProductName: productName,
				Quantity:    item.Quantity,
			}
		}

		update := CartUpdate{
			Count: cartItemsCount,
			Items: cartItems,
		}

		data, _ := json.Marshal(update)
		fmt.Fprintf(w, "data: %s\n\n", data)
		flusher.Flush()
	}

	// Listen for updates
	for {
		select {
		case update := <-clientChan:
			data, err := json.Marshal(update)
			if err != nil {
				log.WithError(err).Error("Failed to marshal cart update")
				continue
			}

			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()

		case <-r.Context().Done():
			return
		}
	}
}

func (fe *frontendServer) notifyCartUpdate(userID string, cart []*pb.CartItem) {
	cartItemsCount := cartSize(cart)

	log.WithFields(logrus.Fields{
		"user_id":          userID,
		"cart_items_count": cartItemsCount,
	}).Info("notifyCartUpdate called")

	if clientChan, ok := fe.cartUpdateClients.Load(userID); ok {
		log.WithFields(logrus.Fields{
			"user_id":          userID,
			"cart_items_count": cartItemsCount,
		}).Info("found SSE client for user, sending update")

		// Convert protobuf cart items to serializable format with product names
		cartItems := make([]CartItem, len(cart))
		for i, item := range cart {
			productName := fe.getProductName(item.ProductId)
			cartItems[i] = CartItem{
				ProductID:   item.ProductId,
				ProductName: productName,
				Quantity:    item.Quantity,
			}
		}

		update := CartUpdate{
			Count: cartItemsCount,
			Items: cartItems,
		}

		select {
		case clientChan.(chan CartUpdate) <- update:
			log.WithFields(logrus.Fields{
				"user_id":          userID,
				"cart_items_count": cartItemsCount,
			}).Info("successfully sent cart update via SSE")
		default:
			log.WithField("user_id", userID).Warn("failed to send cart update, channel full")
		}
	} else {
		log.WithField("user_id", userID).Debug("no SSE client found for user")
	}
}

func (fe *frontendServer) getProductName(productID string) string {
	// Try to get product name from product catalog service
	ctx, cancel := context.WithTimeout(context.Background(), time.Second*2)
	defer cancel()

	client := pb.NewProductCatalogServiceClient(fe.productCatalogSvcConn)
	resp, err := client.GetProduct(ctx, &pb.GetProductRequest{Id: productID})
	if err != nil {
		log.WithError(err).WithField("product_id", productID).Warn("failed to get product name")
		return productID // fallback to product ID
	}

	return resp.Name
}

func main() {
	ctx := context.Background()
	log := logrus.New()
	log.Level = logrus.DebugLevel
	log.Formatter = &logrus.JSONFormatter{
		FieldMap: logrus.FieldMap{
			logrus.FieldKeyTime:  "timestamp",
			logrus.FieldKeyLevel: "severity",
			logrus.FieldKeyMsg:   "message",
		},
		TimestampFormat: time.RFC3339Nano,
	}
	log.Out = os.Stdout

	svc := new(frontendServer)

	otel.SetTextMapPropagator(
		propagation.NewCompositeTextMapPropagator(
			propagation.TraceContext{}, propagation.Baggage{}))

	baseUrl = os.Getenv("BASE_URL")

	if os.Getenv("ENABLE_TRACING") == "1" {
		log.Info("Tracing enabled.")
		initTracing(log, ctx, svc)
	} else {
		log.Info("Tracing disabled.")
	}

	if os.Getenv("ENABLE_PROFILER") == "1" {
		log.Info("Profiling enabled.")
		go initProfiling(log, "frontend", "1.0.0")
	} else {
		log.Info("Profiling disabled.")
	}

	srvPort := port
	if os.Getenv("PORT") != "" {
		srvPort = os.Getenv("PORT")
	}
	addr := os.Getenv("LISTEN_ADDR")
	mustMapEnv(&svc.productCatalogSvcAddr, "PRODUCT_CATALOG_SERVICE_ADDR")
	mustMapEnv(&svc.currencySvcAddr, "CURRENCY_SERVICE_ADDR")
	mustMapEnv(&svc.cartSvcAddr, "CART_SERVICE_ADDR")
	mustMapEnv(&svc.recommendationSvcAddr, "RECOMMENDATION_SERVICE_ADDR")
	mustMapEnv(&svc.checkoutSvcAddr, "CHECKOUT_SERVICE_ADDR")
	mustMapEnv(&svc.shippingSvcAddr, "SHIPPING_SERVICE_ADDR")
	mustMapEnv(&svc.adSvcAddr, "AD_SERVICE_ADDR")
	mustMapEnv(&svc.recipeSvcAddr, "RECIPE_SERVICE_ADDR")
	mustMapEnv(&svc.shoppingAssistantSvcAddr, "SHOPPING_ASSISTANT_SERVICE_ADDR")

	mustConnGRPC(ctx, &svc.currencySvcConn, svc.currencySvcAddr)
	mustConnGRPC(ctx, &svc.productCatalogSvcConn, svc.productCatalogSvcAddr)
	mustConnGRPC(ctx, &svc.cartSvcConn, svc.cartSvcAddr)
	mustConnGRPC(ctx, &svc.recommendationSvcConn, svc.recommendationSvcAddr)
	mustConnGRPC(ctx, &svc.shippingSvcConn, svc.shippingSvcAddr)
	mustConnGRPC(ctx, &svc.checkoutSvcConn, svc.checkoutSvcAddr)
	mustConnGRPC(ctx, &svc.adSvcConn, svc.adSvcAddr)
	mustConnGRPC(ctx, &svc.recipeSvcConn, svc.recipeSvcAddr)

	r := mux.NewRouter()
	r.HandleFunc(baseUrl+"/", svc.homeHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/product/{id}", svc.productHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/cart", svc.viewCartHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/cart", svc.addToCartHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/cart/empty", svc.emptyCartHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/setCurrency", svc.setCurrencyHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/logout", svc.logoutHandler).Methods(http.MethodGet)
	r.HandleFunc(baseUrl+"/cart/checkout", svc.placeOrderHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/recipes", svc.recipesHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/recipe/{id}", svc.recipeDetailHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/recipe/{id}/add-to-cart", svc.addRecipeToCartHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/suggested-recipe/{id}", svc.suggestedRecipeDetailHandler).Methods(http.MethodGet, http.MethodHead)
	r.HandleFunc(baseUrl+"/suggested-recipe/{id}/add-to-cart", svc.addSuggestedRecipeToCartHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/suggested-recipes", svc.suggestedRecipesHandler).Methods(http.MethodPost)
	r.HandleFunc(baseUrl+"/cart/updates", svc.cartUpdatesHandler).Methods(http.MethodGet)
	r.HandleFunc(baseUrl+"/assistant", svc.assistantHandler).Methods(http.MethodGet, http.MethodHead)
	r.PathPrefix(baseUrl + "/static/").Handler(http.StripPrefix(baseUrl+"/static/", http.FileServer(http.Dir("./static/"))))
	r.HandleFunc(baseUrl+"/robots.txt", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, "User-agent: *\nDisallow: /") })
	r.HandleFunc(baseUrl+"/_healthz", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, "ok") })
	r.HandleFunc(baseUrl+"/product-meta/{ids}", svc.getProductByID).Methods(http.MethodGet)
	r.HandleFunc(baseUrl+"/bot", svc.chatBotHandler).Methods(http.MethodPost)

	var handler http.Handler = r
	handler = &logHandler{log: log, next: handler}     // add logging
	handler = ensureSessionID(handler)                 // add session ID
	handler = otelhttp.NewHandler(handler, "frontend") // add OTel tracing

	log.Infof("starting server on " + addr + ":" + srvPort)
	log.Fatal(http.ListenAndServe(addr+":"+srvPort, handler))
}
func initStats(log logrus.FieldLogger) {
	// TODO(arbrown) Implement OpenTelemtry stats
}

func initTracing(log logrus.FieldLogger, ctx context.Context, svc *frontendServer) (*sdktrace.TracerProvider, error) {
	mustMapEnv(&svc.collectorAddr, "COLLECTOR_SERVICE_ADDR")
	mustConnGRPC(ctx, &svc.collectorConn, svc.collectorAddr)
	exporter, err := otlptracegrpc.New(
		ctx,
		otlptracegrpc.WithGRPCConn(svc.collectorConn))
	if err != nil {
		log.Warnf("warn: Failed to create trace exporter: %v", err)
	}
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithSampler(sdktrace.AlwaysSample()))
	otel.SetTracerProvider(tp)

	return tp, err
}

func initProfiling(log logrus.FieldLogger, service, version string) {
	// TODO(ahmetb) this method is duplicated in other microservices using Go
	// since they are not sharing packages.
	for i := 1; i <= 3; i++ {
		log = log.WithField("retry", i)
		if err := profiler.Start(profiler.Config{
			Service:        service,
			ServiceVersion: version,
			// ProjectID must be set if not running on GCP.
			// ProjectID: "my-project",
		}); err != nil {
			log.Warnf("warn: failed to start profiler: %+v", err)
		} else {
			log.Info("started Stackdriver profiler")
			return
		}
		d := time.Second * 10 * time.Duration(i)
		log.Debugf("sleeping %v to retry initializing Stackdriver profiler", d)
		time.Sleep(d)
	}
	log.Warn("warning: could not initialize Stackdriver profiler after retrying, giving up")
}

func mustMapEnv(target *string, envKey string) {
	v := os.Getenv(envKey)
	if v == "" {
		panic(fmt.Sprintf("environment variable %q not set", envKey))
	}
	*target = v
}

func mustConnGRPC(ctx context.Context, conn **grpc.ClientConn, addr string) {
	var err error
	ctx, cancel := context.WithTimeout(ctx, time.Second*3)
	defer cancel()
	*conn, err = grpc.DialContext(ctx, addr,
		grpc.WithInsecure(),
		grpc.WithUnaryInterceptor(otelgrpc.UnaryClientInterceptor()),
		grpc.WithStreamInterceptor(otelgrpc.StreamClientInterceptor()))
	if err != nil {
		panic(errors.Wrapf(err, "grpc: failed to connect %s", addr))
	}
}
