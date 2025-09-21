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
	"html/template"
	"io"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/mux"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"

	pb "github.com/GoogleCloudPlatform/microservices-demo/src/frontend/genproto"
	"github.com/GoogleCloudPlatform/microservices-demo/src/frontend/money"
	"github.com/GoogleCloudPlatform/microservices-demo/src/frontend/validator"
)

type platformDetails struct {
	css      string
	provider string
}

var (
	frontendMessage  = strings.TrimSpace(os.Getenv("FRONTEND_MESSAGE"))
	isCymbalBrand    = "true" == strings.ToLower(os.Getenv("CYMBAL_BRANDING"))
	assistantEnabled = "true" == strings.ToLower(os.Getenv("ENABLE_ASSISTANT"))
	templates        = template.Must(template.New("").
				Funcs(template.FuncMap{
			"renderMoney":        renderMoney,
			"renderCurrencyLogo": renderCurrencyLogo,
		}).ParseGlob("templates/*.html"))
	plat platformDetails
)

var validEnvs = []string{"local", "gcp", "azure", "aws", "onprem", "alibaba"}

func (fe *frontendServer) homeHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.WithField("currency", currentCurrency(r)).Info("home")
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	products, err := fe.getProducts(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve products"), http.StatusInternalServerError)
		return
	}
	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	type productView struct {
		Item  *pb.Product
		Price *pb.Money
	}
	ps := make([]productView, len(products))
	for i, p := range products {
		price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "failed to do currency conversion for product %s", p.GetId()), http.StatusInternalServerError)
			return
		}
		ps[i] = productView{p, price}
	}

	// Set ENV_PLATFORM (default to local if not set; use env var if set; otherwise detect GCP, which overrides env)_
	var env = os.Getenv("ENV_PLATFORM")
	// Only override from env variable if set + valid env
	if env == "" || stringinSlice(validEnvs, env) == false {
		fmt.Println("env platform is either empty or invalid")
		env = "local"
	}
	// Autodetect GCP
	addrs, err := net.LookupHost("metadata.google.internal.")
	if err == nil && len(addrs) >= 0 {
		log.Debugf("Detected Google metadata server: %v, setting ENV_PLATFORM to GCP.", addrs)
		env = "gcp"
	}

	log.Debugf("ENV_PLATFORM is: %s", env)
	plat = platformDetails{}
	plat.setPlatformDetails(strings.ToLower(env))

	if err := templates.ExecuteTemplate(w, "home", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency": true,
		"currencies":    currencies,
		"products":      ps,
		"cart_size":     cartSize(cart),
		"banner_color":  os.Getenv("BANNER_COLOR"), // illustrates canary deployments
		"ad":            fe.chooseAd(r.Context(), []string{}, log),
	})); err != nil {
		log.Error(err)
	}
}

func (plat *platformDetails) setPlatformDetails(env string) {
	if env == "aws" {
		plat.provider = "AWS"
		plat.css = "aws-platform"
	} else if env == "onprem" {
		plat.provider = "On-Premises"
		plat.css = "onprem-platform"
	} else if env == "azure" {
		plat.provider = "Azure"
		plat.css = "azure-platform"
	} else if env == "gcp" {
		plat.provider = "Google Cloud"
		plat.css = "gcp-platform"
	} else if env == "alibaba" {
		plat.provider = "Alibaba Cloud"
		plat.css = "alibaba-platform"
	} else {
		plat.provider = "local"
		plat.css = "local"
	}
}

func (fe *frontendServer) productHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	if id == "" {
		renderHTTPError(log, r, w, errors.New("product id not specified"), http.StatusBadRequest)
		return
	}
	log.WithField("id", id).WithField("currency", currentCurrency(r)).
		Debug("serving product page")

	p, err := fe.getProduct(r.Context(), id)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve product"), http.StatusInternalServerError)
		return
	}
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to convert currency"), http.StatusInternalServerError)
		return
	}

	// ignores the error retrieving recommendations since it is not critical
	recommendations, err := fe.getRecommendations(r.Context(), sessionID(r), []string{id})
	if err != nil {
		log.WithField("error", err).Warn("failed to get product recommendations")
	}

	product := struct {
		Item  *pb.Product
		Price *pb.Money
	}{p, price}

	// Fetch packaging info (weight/dimensions) of the product
	// The packaging service is an optional microservice you can run as part of a Google Cloud demo.
	var packagingInfo *PackagingInfo = nil
	if isPackagingServiceConfigured() {
		packagingInfo, err = httpGetPackagingInfo(id)
		if err != nil {
			fmt.Println("Failed to obtain product's packaging info:", err)
		}
	}

	if err := templates.ExecuteTemplate(w, "product", injectCommonTemplateData(r, map[string]interface{}{
		"ad":              fe.chooseAd(r.Context(), p.Categories, log),
		"show_currency":   true,
		"currencies":      currencies,
		"product":         product,
		"recommendations": recommendations,
		"cart_size":       cartSize(cart),
		"packagingInfo":   packagingInfo,
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) addToCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	quantity, _ := strconv.ParseUint(r.FormValue("quantity"), 10, 32)
	productID := r.FormValue("product_id")
	payload := validator.AddToCartPayload{
		Quantity:  quantity,
		ProductID: productID,
	}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}
	log.WithField("product", payload.ProductID).WithField("quantity", payload.Quantity).Debug("adding to cart")

	p, err := fe.getProduct(r.Context(), payload.ProductID)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve product"), http.StatusInternalServerError)
		return
	}

	if err := fe.insertCart(r.Context(), sessionID(r), p.GetId(), int32(payload.Quantity)); err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to add to cart"), http.StatusInternalServerError)
		return
	}
	w.Header().Set("location", baseUrl+"/cart")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) emptyCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("emptying cart")

	if err := fe.emptyCart(r.Context(), sessionID(r)); err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to empty cart"), http.StatusInternalServerError)
		return
	}
	w.Header().Set("location", baseUrl+"/")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) viewCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("view user cart")
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}
	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	// ignores the error retrieving recommendations since it is not critical
	recommendations, err := fe.getRecommendations(r.Context(), sessionID(r), cartIDs(cart))
	if err != nil {
		log.WithField("error", err).Warn("failed to get product recommendations")
	}

	shippingCost, err := fe.getShippingQuote(r.Context(), cart, currentCurrency(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to get shipping quote"), http.StatusInternalServerError)
		return
	}

	type cartItemView struct {
		Item     *pb.Product
		Quantity int32
		Price    *pb.Money
	}
	items := make([]cartItemView, len(cart))
	totalPrice := pb.Money{CurrencyCode: currentCurrency(r)}
	for i, item := range cart {
		p, err := fe.getProduct(r.Context(), item.GetProductId())
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "could not retrieve product #%s", item.GetProductId()), http.StatusInternalServerError)
			return
		}
		price, err := fe.convertCurrency(r.Context(), p.GetPriceUsd(), currentCurrency(r))
		if err != nil {
			renderHTTPError(log, r, w, errors.Wrapf(err, "could not convert currency for product #%s", item.GetProductId()), http.StatusInternalServerError)
			return
		}

		multPrice := money.MultiplySlow(*price, uint32(item.GetQuantity()))
		items[i] = cartItemView{
			Item:     p,
			Quantity: item.GetQuantity(),
			Price:    &multPrice}
		totalPrice = money.Must(money.Sum(totalPrice, multPrice))
	}
	totalPrice = money.Must(money.Sum(totalPrice, *shippingCost))
	year := time.Now().Year()

	if err := templates.ExecuteTemplate(w, "cart", injectCommonTemplateData(r, map[string]interface{}{
		"currencies":       currencies,
		"recommendations":  recommendations,
		"cart_size":        cartSize(cart),
		"shipping_cost":    shippingCost,
		"show_currency":    true,
		"total_cost":       totalPrice,
		"items":            items,
		"expiration_years": []int{year, year + 1, year + 2, year + 3, year + 4},
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) placeOrderHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("placing order")

	var (
		email         = r.FormValue("email")
		streetAddress = r.FormValue("street_address")
		zipCode, _    = strconv.ParseInt(r.FormValue("zip_code"), 10, 32)
		city          = r.FormValue("city")
		state         = r.FormValue("state")
		country       = r.FormValue("country")
		ccNumber      = r.FormValue("credit_card_number")
		ccMonth, _    = strconv.ParseInt(r.FormValue("credit_card_expiration_month"), 10, 32)
		ccYear, _     = strconv.ParseInt(r.FormValue("credit_card_expiration_year"), 10, 32)
		ccCVV, _      = strconv.ParseInt(r.FormValue("credit_card_cvv"), 10, 32)
	)

	payload := validator.PlaceOrderPayload{
		Email:         email,
		StreetAddress: streetAddress,
		ZipCode:       zipCode,
		City:          city,
		State:         state,
		Country:       country,
		CcNumber:      ccNumber,
		CcMonth:       ccMonth,
		CcYear:        ccYear,
		CcCVV:         ccCVV,
	}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}

	order, err := pb.NewCheckoutServiceClient(fe.checkoutSvcConn).
		PlaceOrder(r.Context(), &pb.PlaceOrderRequest{
			Email: payload.Email,
			CreditCard: &pb.CreditCardInfo{
				CreditCardNumber:          payload.CcNumber,
				CreditCardExpirationMonth: int32(payload.CcMonth),
				CreditCardExpirationYear:  int32(payload.CcYear),
				CreditCardCvv:             int32(payload.CcCVV)},
			UserId:       sessionID(r),
			UserCurrency: currentCurrency(r),
			Address: &pb.Address{
				StreetAddress: payload.StreetAddress,
				City:          payload.City,
				State:         payload.State,
				ZipCode:       int32(payload.ZipCode),
				Country:       payload.Country},
		})
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to complete the order"), http.StatusInternalServerError)
		return
	}
	log.WithField("order", order.GetOrder().GetOrderId()).Info("order placed")

	order.GetOrder().GetItems()
	recommendations, _ := fe.getRecommendations(r.Context(), sessionID(r), nil)

	totalPaid := *order.GetOrder().GetShippingCost()
	for _, v := range order.GetOrder().GetItems() {
		multPrice := money.MultiplySlow(*v.GetCost(), uint32(v.GetItem().GetQuantity()))
		totalPaid = money.Must(money.Sum(totalPaid, multPrice))
	}

	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	if err := templates.ExecuteTemplate(w, "order", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency":   false,
		"currencies":      currencies,
		"order":           order.GetOrder(),
		"total_paid":      &totalPaid,
		"recommendations": recommendations,
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) assistantHandler(w http.ResponseWriter, r *http.Request) {
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	if err := templates.ExecuteTemplate(w, "assistant", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency": false,
		"currencies":    currencies,
	})); err != nil {
		log.Println(err)
	}
}

func (fe *frontendServer) logoutHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Debug("logging out")
	for _, c := range r.Cookies() {
		c.Expires = time.Now().Add(-time.Hour * 24 * 365)
		c.MaxAge = -1
		http.SetCookie(w, c)
	}
	w.Header().Set("Location", baseUrl+"/")
	w.WriteHeader(http.StatusFound)
}

func (fe *frontendServer) getProductByID(w http.ResponseWriter, r *http.Request) {
	id := mux.Vars(r)["ids"]
	if id == "" {
		return
	}

	p, err := fe.getProduct(r.Context(), id)
	if err != nil {
		return
	}

	jsonData, err := json.Marshal(p)
	if err != nil {
		fmt.Println(err)
		return
	}

	w.Write(jsonData)
	w.WriteHeader(http.StatusOK)
}

func (fe *frontendServer) chatBotHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	type Response struct {
		Message string `json:"message"`
	}

	type LLMResponse struct {
		Content string         `json:"content"`
		Details map[string]any `json:"details"`
	}

	var response LLMResponse

	url := "http://" + fe.shoppingAssistantSvcAddr
	req, err := http.NewRequest(http.MethodPost, url, r.Body)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to create request"), http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to send request"), http.StatusInternalServerError)
		return
	}

	body, err := io.ReadAll(res.Body)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to read response"), http.StatusInternalServerError)
		return
	}

	fmt.Printf("%+v\n", body)
	fmt.Printf("%+v\n", res)

	err = json.Unmarshal(body, &response)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "failed to unmarshal body"), http.StatusInternalServerError)
		return
	}

	// respond with the same message
	json.NewEncoder(w).Encode(Response{Message: response.Content})

	w.WriteHeader(http.StatusOK)
}

func (fe *frontendServer) setCurrencyHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	cur := r.FormValue("currency_code")
	payload := validator.SetCurrencyPayload{Currency: cur}
	if err := payload.Validate(); err != nil {
		renderHTTPError(log, r, w, validator.ValidationErrorResponse(err), http.StatusUnprocessableEntity)
		return
	}
	log.WithField("curr.new", payload.Currency).WithField("curr.old", currentCurrency(r)).
		Debug("setting currency")

	if payload.Currency != "" {
		http.SetCookie(w, &http.Cookie{
			Name:   cookieCurrency,
			Value:  payload.Currency,
			MaxAge: cookieMaxAge,
		})
	}
	referer := r.Header.Get("referer")
	if referer == "" {
		referer = baseUrl + "/"
	}
	w.Header().Set("Location", referer)
	w.WriteHeader(http.StatusFound)
}

// chooseAd queries for advertisements available and randomly chooses one, if
// available. It ignores the error retrieving the ad since it is not critical.
func (fe *frontendServer) chooseAd(ctx context.Context, ctxKeys []string, log logrus.FieldLogger) *pb.Ad {
	ads, err := fe.getAd(ctx, ctxKeys)
	if err != nil {
		log.WithField("error", err).Warn("failed to retrieve ads")
		return nil
	}
	return ads[rand.Intn(len(ads))]
}

func renderHTTPError(log logrus.FieldLogger, r *http.Request, w http.ResponseWriter, err error, code int) {
	log.WithField("error", err).Error("request error")
	errMsg := fmt.Sprintf("%+v", err)

	w.WriteHeader(code)

	if templateErr := templates.ExecuteTemplate(w, "error", injectCommonTemplateData(r, map[string]interface{}{
		"error":       errMsg,
		"status_code": code,
		"status":      http.StatusText(code),
	})); templateErr != nil {
		log.Println(templateErr)
	}
}

func injectCommonTemplateData(r *http.Request, payload map[string]interface{}) map[string]interface{} {
	data := map[string]interface{}{
		"session_id":        sessionID(r),
		"request_id":        r.Context().Value(ctxKeyRequestID{}),
		"user_currency":     currentCurrency(r),
		"platform_css":      plat.css,
		"platform_name":     plat.provider,
		"is_cymbal_brand":   isCymbalBrand,
		"assistant_enabled": assistantEnabled,
		"deploymentDetails": deploymentDetailsMap,
		"frontendMessage":   frontendMessage,
		"currentYear":       time.Now().Year(),
		"baseUrl":           baseUrl,
	}

	for k, v := range payload {
		data[k] = v
	}

	return data
}

func currentCurrency(r *http.Request) string {
	c, _ := r.Cookie(cookieCurrency)
	if c != nil {
		return c.Value
	}
	return defaultCurrency
}

func sessionID(r *http.Request) string {
	v := r.Context().Value(ctxKeySessionID{})
	if v != nil {
		return v.(string)
	}
	return ""
}

func cartIDs(c []*pb.CartItem) []string {
	out := make([]string, len(c))
	for i, v := range c {
		out[i] = v.GetProductId()
	}
	return out
}

// get total # of items in cart
func cartSize(c []*pb.CartItem) int {
	cartSize := 0
	for _, item := range c {
		cartSize += int(item.GetQuantity())
	}
	return cartSize
}

func renderMoney(money pb.Money) string {
	currencyLogo := renderCurrencyLogo(money.GetCurrencyCode())
	return fmt.Sprintf("%s%d.%02d", currencyLogo, money.GetUnits(), money.GetNanos()/10000000)
}

func renderCurrencyLogo(currencyCode string) string {
	logos := map[string]string{
		"USD": "$",
		"CAD": "$",
		"JPY": "¥",
		"EUR": "€",
		"TRY": "₺",
		"GBP": "£",
	}

	logo := "$" //default
	if val, ok := logos[currencyCode]; ok {
		logo = val
	}
	return logo
}

func stringinSlice(slice []string, val string) bool {
	for _, item := range slice {
		if item == val {
			return true
		}
	}
	return false
}

// Recipe handlers
func (fe *frontendServer) recipesHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log.Info("[Recipe List] fetching recipes")

	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	// Call RecipeService to get list of recipes
	client := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	resp, err := client.ListRecipes(r.Context(), &pb.ListRecipesRequest{})
	if err != nil {
		log.WithError(err).Error("failed to list recipes")
		renderHTTPError(log, r, w, errors.Wrap(err, "could not list recipes"), http.StatusInternalServerError)
		return
	}

	if err := templates.ExecuteTemplate(w, "recipe-list", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency": true,
		"currencies":    currencies,
		"cart_size":     cartSize(cart),
		"recipes":       resp.Recipes,
	})); err != nil {
		log.WithError(err).Error("failed to render recipe list")
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (fe *frontendServer) recipeDetailHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	if id == "" {
		renderHTTPError(log, r, w, errors.New("recipe id not specified"), http.StatusBadRequest)
		return
	}

	log.WithField("id", id).Info("[Recipe Detail] fetching recipe")

	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	cart, err := fe.getCart(r.Context(), sessionID(r))
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	// Call RecipeService to get recipe details
	client := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	resp, err := client.GetRecipe(r.Context(), &pb.GetRecipeRequest{RecipeId: id})
	if err != nil {
		log.WithError(err).Error("failed to get recipe")
		renderHTTPError(log, r, w, errors.Wrap(err, "could not get recipe"), http.StatusInternalServerError)
		return
	}

	// Create a map of cart product IDs to quantities for easy lookup
	cartProductMap := make(map[string]int32)
	cartProductNames := make(map[string]string) // productId -> productName
	for _, item := range cart {
		cartProductMap[item.ProductId] = item.Quantity

		// Get product details to get the name
		product, err := fe.getProduct(r.Context(), item.ProductId)
		if err != nil {
			log.WithError(err).WithField("product_id", item.ProductId).Warn("could not get product details for cart item")
			continue
		}
		cartProductNames[item.ProductId] = strings.ToLower(product.Name)
	}

	// Create a map of ingredient names to cart info for template use
	ingredientCartStatus := make(map[string]map[string]interface{})
	for _, ingredient := range resp.Recipe.Ingredients {
		ingredientName := strings.ToLower(ingredient.Name)

		// Check if this ingredient matches any product in the cart
		for productId, productName := range cartProductNames {
			if strings.Contains(productName, ingredientName) || strings.Contains(ingredientName, strings.Fields(productName)[0]) {
				ingredientCartStatus[ingredient.Name] = map[string]interface{}{
					"in_cart":    true,
					"quantity":   cartProductMap[productId],
					"product_id": productId,
				}
				break
			}
		}
	}

	if err := templates.ExecuteTemplate(w, "recipe-detail", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency":          true,
		"currencies":             currencies,
		"cart_size":              cartSize(cart),
		"recipe":                 resp.Recipe,
		"added":                  r.URL.Query().Get("added") == "true",
		"ingredient_cart_status": ingredientCartStatus,
	})); err != nil {
		log.WithError(err).Error("failed to render recipe detail")
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (fe *frontendServer) addRecipeToCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	if id == "" {
		renderHTTPError(log, r, w, errors.New("recipe id not specified"), http.StatusBadRequest)
		return
	}

	// Parse servings from form data
	servings := int32(4) // default
	if servingsStr := r.FormValue("servings"); servingsStr != "" {
		if parsedServings, err := strconv.ParseInt(servingsStr, 10, 32); err == nil {
			servings = int32(parsedServings)
		}
	}

	// Get selected ingredients from form data
	selectedIngredients := r.FormValue("ingredient_list")

	// If ingredient_list is empty, try to get individual checkbox values as fallback
	// Note: Only checked checkboxes will have values in the form
	if selectedIngredients == "" {
		selectedCheckboxes := r.Form["selected_ingredients"]
		log.WithField("raw_checkboxes", selectedCheckboxes).Debug("[Recipe] fallback checkbox processing")
		if len(selectedCheckboxes) > 0 {
			// Filter out empty values (unchecked checkboxes don't send values)
			var validIngredients []string
			for _, ingredient := range selectedCheckboxes {
				if strings.TrimSpace(ingredient) != "" {
					validIngredients = append(validIngredients, ingredient)
				}
			}
			if len(validIngredients) > 0 {
				selectedIngredients = strings.Join(validIngredients, ", ")
			}
		}
	}

	// Debug logging to see what was received
	log.WithFields(logrus.Fields{
		"ingredient_list_field": r.FormValue("ingredient_list"),
		"checkbox_values":       r.Form["selected_ingredients"],
		"final_selected":        selectedIngredients,
	}).Debug("[Recipe] form data received")

	if selectedIngredients == "" {
		renderHTTPError(log, r, w, errors.New("no ingredients selected"), http.StatusBadRequest)
		return
	}

	log.WithFields(logrus.Fields{
		"recipe_id":            id,
		"servings":             servings,
		"user":                 sessionID(r),
		"selected_ingredients": selectedIngredients,
	}).Info("[Recipe] adding selected recipe ingredients to cart")

	// Build recipe text with selected ingredients for processing
	recipeText := fmt.Sprintf("Add selected ingredients to cart (serves %d): %s",
		servings, selectedIngredients)

	// Call RecipeService to process ONLY the selected ingredients
	// Don't pass RecipeId to avoid the service using the full recipe
	recipeClient := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	_, err := recipeClient.ProcessRecipeRequest(r.Context(), &pb.ProcessRecipeRequestMessage{
		Message:  recipeText, // Use the message field for the ingredient list
		Servings: servings,
		UserId:   sessionID(r),
		// Deliberately NOT setting RecipeId so it only processes the selected ingredients
	})
	if err != nil {
		log.WithError(err).Error("failed to add recipe to cart")
		renderHTTPError(log, r, w, errors.Wrap(err, "could not add recipe to cart"), http.StatusInternalServerError)
		return
	}

	// Wait for cart to be updated and then notify SSE clients
	go func() {
		userID := sessionID(r)
		// Wait a moment for the async cart operations to complete
		time.Sleep(2 * time.Second)

		// Get updated cart and notify SSE clients
		if updatedCart, err := fe.getCart(context.Background(), userID); err == nil {
			fe.notifyCartUpdate(userID, updatedCart)
		}
	}()

	// Redirect back to recipe detail page with success message
	http.Redirect(w, r, fmt.Sprintf("%s/recipe/%s?added=true", baseUrl, id), http.StatusFound)
}

func (fe *frontendServer) suggestedRecipesHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	log = log.WithField("handler", "suggested-recipes")

	if r.Method != http.MethodPost {
		renderHTTPError(log, r, w, errors.New("method not allowed"), http.StatusMethodNotAllowed)
		return
	}

	// Parse request body
	var req struct {
		CartItems []string `json:"cart_items"`
		SessionID string   `json:"session_id"`
	}

	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&req); err != nil {
		log.WithError(err).Error("failed to decode request")
		renderHTTPError(log, r, w, errors.Wrap(err, "invalid request"), http.StatusBadRequest)
		return
	}

	// Validate request
	if len(req.CartItems) < 2 {
		// Return empty result for insufficient ingredients
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	log.WithFields(logrus.Fields{
		"cart_items_count": len(req.CartItems),
		"session_id":       req.SessionID,
		"ingredients":      req.CartItems,
	}).Info("requesting suggested recipes")

	// Call RecipeService for suggested recipes with extended timeout for image generation
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	recipeClient := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	recipeResp, err := recipeClient.GetSuggestedRecipes(ctx, &pb.SuggestedRecipesRequest{
		CartItems: req.CartItems,
		SessionId: req.SessionID,
	})

	if err != nil {
		log.WithError(err).Error("failed to get suggested recipes")
		// Return empty result instead of error to gracefully degrade
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	// Convert protobuf recipes to JSON-friendly format and cache them
	var jsonRecipes []map[string]interface{}
	var cachedRecipes []CachedRecipe
	sessionId := sessionID(r)

	for _, recipe := range recipeResp.Recipes {
		jsonRecipe := map[string]interface{}{
			"recipe_id":        recipe.RecipeId,
			"title":            recipe.Title,
			"description":      recipe.Description,
			"cook_time":        recipe.CookTime,
			"default_servings": recipe.DefaultServings,
			"ingredients":      recipe.Ingredients,
			"instructions":     recipe.Instructions,
			"image_data":       recipe.ImageData, // Include image data in JSON response
		}
		jsonRecipes = append(jsonRecipes, jsonRecipe)

		// Create cached recipe for storage
		cachedRecipe := CachedRecipe{
			RecipeId:        recipe.RecipeId,
			Title:           recipe.Title,
			Description:     recipe.Description,
			CookTime:        recipe.CookTime,
			DefaultServings: recipe.DefaultServings,
			Ingredients:     convertToCachedIngredients(recipe.Ingredients),
			Instructions:    recipe.Instructions,
			SessionID:       sessionId,
			CreatedAt:       time.Now(),
			ImageData:       recipe.ImageData, // Include image data in cached recipe
		}
		cachedRecipes = append(cachedRecipes, cachedRecipe)
	}

	// Cache the suggested recipes for this session
	fe.suggestedRecipesCache.Store(sessionId, cachedRecipes)

	log.WithField("suggested_recipes_count", len(jsonRecipes)).Info("returning suggested recipes")

	// Return JSON response
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(jsonRecipes); err != nil {
		log.WithError(err).Error("failed to encode response")
	}
}

// Helper function to convert protobuf ingredients to cached ingredient format
func convertToCachedIngredients(ingredients []*pb.Ingredient) []*CachedIngredient {
	var result []*CachedIngredient
	for _, ingredient := range ingredients {
		cachedIngredient := &CachedIngredient{
			Name:     ingredient.Name,
			Quantity: ingredient.Quantity,
			Unit:     ingredient.Unit,
		}
		result = append(result, cachedIngredient)
	}
	return result
}

// Handler for individual suggested recipe details
func (fe *frontendServer) suggestedRecipeDetailHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	sessionId := sessionID(r)

	if id == "" {
		renderHTTPError(log, r, w, errors.New("recipe id not specified"), http.StatusBadRequest)
		return
	}

	log.WithFields(logrus.Fields{
		"id":      id,
		"session": sessionId,
	}).Info("[Suggested Recipe Detail] fetching suggested recipe")

	// Get cached suggested recipes for this session
	cached, ok := fe.suggestedRecipesCache.Load(sessionId)
	if !ok {
		renderHTTPError(log, r, w, errors.New("no suggested recipes found for session"), http.StatusNotFound)
		return
	}

	cachedRecipes, ok := cached.([]CachedRecipe)
	if !ok {
		renderHTTPError(log, r, w, errors.New("invalid cached recipes format"), http.StatusInternalServerError)
		return
	}

	// Find the specific recipe
	var recipe *CachedRecipe
	for i := range cachedRecipes {
		if cachedRecipes[i].RecipeId == id {
			recipe = &cachedRecipes[i]
			break
		}
	}

	if recipe == nil {
		renderHTTPError(log, r, w, errors.New("suggested recipe not found"), http.StatusNotFound)
		return
	}

	// Get currencies and cart (same as regular recipe handler)
	currencies, err := fe.getCurrencies(r.Context())
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve currencies"), http.StatusInternalServerError)
		return
	}

	cart, err := fe.getCart(r.Context(), sessionId)
	if err != nil {
		renderHTTPError(log, r, w, errors.Wrap(err, "could not retrieve cart"), http.StatusInternalServerError)
		return
	}

	// Create a map of cart product IDs to quantities for easy lookup
	cartProductMap := make(map[string]int32)
	cartProductNames := make(map[string]string) // productId -> productName
	for _, item := range cart {
		cartProductMap[item.ProductId] = item.Quantity

		// Get product details to get the name
		product, err := fe.getProduct(r.Context(), item.ProductId)
		if err != nil {
			log.WithError(err).WithField("product_id", item.ProductId).Warn("could not get product details for cart item")
			continue
		}
		cartProductNames[item.ProductId] = strings.ToLower(product.Name)
	}

	// Create a map of ingredient names to cart info for template use
	ingredientCartStatus := make(map[string]map[string]interface{})

	// For suggested recipes, check ingredient availability using the ingredientmatcher service
	ingredientNames := make([]string, len(recipe.Ingredients))
	for i, ingredient := range recipe.Ingredients {
		ingredientNames[i] = ingredient.Name
	}

	// Call recipe service to check ingredient availability
	recipeClient := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	ingredientList := strings.Join(ingredientNames, ", ")
	checkMessage := fmt.Sprintf("Check ingredient availability: %s", ingredientList)
	
	checkResp, err := recipeClient.ProcessRecipeRequest(r.Context(), &pb.ProcessRecipeRequestMessage{
		Message: checkMessage,
		UserId:  sessionId,
	})

	var unavailableIngredients map[string]bool = make(map[string]bool)
	if err == nil && checkResp != nil {
		// Use the unmatched_ingredients field from the response
		log.WithFields(logrus.Fields{
			"matched_products":     checkResp.MatchedProducts,
			"ingredients":          checkResp.Ingredients,
			"unmatched_ingredients": checkResp.UnmatchedIngredients,
		}).Info("[Suggested Recipe Detail] ingredient availability check completed")
		
		// Mark unmatched ingredients as unavailable
		for _, unmatchedIngredient := range checkResp.UnmatchedIngredients {
			// Find the original recipe ingredient that corresponds to this unmatched ingredient
			for _, recipeIngredient := range recipe.Ingredients {
				ingredientLower := strings.ToLower(recipeIngredient.Name)
				unmatchedLower := strings.ToLower(unmatchedIngredient)
				
				// Check if the cleaned ingredient name is contained in the original ingredient name
				// For example: "Ginger" (unmatched) should match "Grated Fresh Ginger" (original)
				if strings.Contains(ingredientLower, unmatchedLower) || strings.Contains(unmatchedLower, ingredientLower) {
					unavailableIngredients[recipeIngredient.Name] = true
					break
				}
			}
		}
	} else {
		log.WithError(err).Warn("[Suggested Recipe Detail] failed to check ingredient availability, using fallback")
		// Fallback to static logic
		for _, recipeIngredient := range recipe.Ingredients {
			if !fe.isIngredientAvailableInCatalog(strings.ToLower(recipeIngredient.Name)) {
				unavailableIngredients[recipeIngredient.Name] = true
			}
		}
	}

	// Now match ingredients to cart status
	for _, recipeIngredient := range recipe.Ingredients {
		ingredientNameLower := strings.ToLower(recipeIngredient.Name)

		// Find matching products in cart by name similarity
		var matchedProductId string
		var matchedQuantity int32
		for productId, productName := range cartProductNames {
			if strings.Contains(productName, ingredientNameLower) || strings.Contains(ingredientNameLower, productName) {
				matchedProductId = productId
				matchedQuantity = cartProductMap[productId]
				break
			}
		}

		if matchedProductId != "" {
			ingredientCartStatus[recipeIngredient.Name] = map[string]interface{}{
				"in_cart":    true,
				"quantity":   matchedQuantity,
				"product_id": matchedProductId,
			}
		} else if unavailableIngredients[recipeIngredient.Name] {
			ingredientCartStatus[recipeIngredient.Name] = map[string]interface{}{
				"in_cart":       false,
				"not_available": true,
			}
		}
	}

	// Debug: Log the ingredient cart status to see what's being passed to template
	log.WithFields(logrus.Fields{
		"ingredient_cart_status": ingredientCartStatus,
		"unavailable_ingredients": unavailableIngredients,
	}).Info("[Suggested Recipe Detail] final ingredient status before template")

	// Render the recipe detail template
	if err := templates.ExecuteTemplate(w, "recipe-detail", injectCommonTemplateData(r, map[string]interface{}{
		"show_currency":          true,
		"currencies":             currencies,
		"cart_size":              len(cart),
		"recipe":                 recipe,
		"suggested":              true, // Flag to indicate this is a suggested recipe
		"ingredient_cart_status": ingredientCartStatus,
	})); err != nil {
		log.WithError(err).Error("failed to render suggested recipe template")
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// Check if an ingredient is likely available in the product catalog
func (fe *frontendServer) isIngredientAvailableInCatalog(ingredientName string) bool {
	// List of ingredients that are commonly not available in grocery catalogs
	// Based on the ingredient matcher's mapping, these are typically not stocked
	unavailableIngredients := []string{
		"dried herbs", "fresh herbs", "mixed herbs", "herbs",
		"salt", "pepper", "seasoning", "spice", "spices",
		"garlic powder", "onion powder", "dried", "fresh",
		"chopped", "minced", "ground", "extract", "essence",
		"flavoring", "vanilla", "baking powder", "baking soda",
		"yeast", "water", "ice", "stock", "broth",
	}

	ingredientLower := strings.ToLower(ingredientName)
	
	// Check if ingredient contains any unavailable terms
	for _, unavailable := range unavailableIngredients {
		if strings.Contains(ingredientLower, unavailable) {
			return false
		}
	}
	
	// For other ingredients, assume they might be available
	return true
}

// Handler for adding suggested recipe ingredients to cart
func (fe *frontendServer) addSuggestedRecipeToCartHandler(w http.ResponseWriter, r *http.Request) {
	log := r.Context().Value(ctxKeyLog{}).(logrus.FieldLogger)
	id := mux.Vars(r)["id"]
	sessionId := sessionID(r)

	if id == "" {
		renderHTTPError(log, r, w, errors.New("recipe id not specified"), http.StatusBadRequest)
		return
	}

	// Parse servings from form data
	servings := int32(4) // default
	if servingsStr := r.FormValue("servings"); servingsStr != "" {
		if parsedServings, err := strconv.ParseInt(servingsStr, 10, 32); err == nil {
			servings = int32(parsedServings)
		}
	}

	// Get selected ingredients from form data
	selectedIngredients := r.FormValue("ingredient_list")

	// If ingredient_list is empty, try to get individual checkbox values as fallback
	if selectedIngredients == "" {
		selectedCheckboxes := r.Form["selected_ingredients"]
		log.WithField("raw_checkboxes", selectedCheckboxes).Debug("[Suggested Recipe] fallback checkbox processing")
		if len(selectedCheckboxes) > 0 {
			var validIngredients []string
			for _, ingredient := range selectedCheckboxes {
				if strings.TrimSpace(ingredient) != "" {
					validIngredients = append(validIngredients, ingredient)
				}
			}
			if len(validIngredients) > 0 {
				selectedIngredients = strings.Join(validIngredients, ", ")
			}
		}
	}

	log.WithFields(logrus.Fields{
		"recipe_id":            id,
		"servings":             servings,
		"selected_ingredients": selectedIngredients,
	}).Info("[Suggested Recipe] adding ingredients to cart")

	if selectedIngredients == "" {
		renderHTTPError(log, r, w, errors.New("no ingredients selected"), http.StatusBadRequest)
		return
	}

	// Get cached suggested recipes for this session
	cached, ok := fe.suggestedRecipesCache.Load(sessionId)
	if !ok {
		renderHTTPError(log, r, w, errors.New("no suggested recipes found for session"), http.StatusNotFound)
		return
	}

	cachedRecipes, ok := cached.([]CachedRecipe)
	if !ok {
		renderHTTPError(log, r, w, errors.New("invalid cached recipes format"), http.StatusInternalServerError)
		return
	}

	// Find the specific recipe
	var recipe *CachedRecipe
	for i := range cachedRecipes {
		if cachedRecipes[i].RecipeId == id {
			recipe = &cachedRecipes[i]
			break
		}
	}

	if recipe == nil {
		renderHTTPError(log, r, w, errors.New("suggested recipe not found"), http.StatusNotFound)
		return
	}

	// Build recipe text with selected ingredients for processing (same format as regular recipe handler)
	recipeText := fmt.Sprintf("Add selected ingredients to cart (serves %d): %s",
		servings, selectedIngredients)

	// Call RecipeService to process the suggested recipe ingredients
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	client := pb.NewRecipeServiceClient(fe.recipeSvcConn)
	_, err := client.ProcessRecipeRequest(ctx, &pb.ProcessRecipeRequestMessage{
		Message:  recipeText, // Use the formatted message instead of raw ingredients
		Servings: servings,
		UserId:   sessionId,
		// Deliberately NOT setting RecipeId to match the working handler pattern
	})

	if err != nil {
		log.WithError(err).Error("failed to process suggested recipe request")
		renderHTTPError(log, r, w, errors.Wrap(err, "could not add suggested recipe ingredients to cart"), http.StatusInternalServerError)
		return
	}

	log.WithField("recipe_id", id).Info("[Suggested Recipe] successfully added ingredients to cart")

	// Wait for cart to be updated and then notify SSE clients
	go func() {
		userID := sessionID(r) // Use sessionID(r) instead of sessionId variable
		log.WithField("user_id", userID).Info("[Suggested Recipe] starting cart notification goroutine")

		// Wait a moment for the async cart operations to complete
		time.Sleep(2 * time.Second)

		// Get updated cart and notify SSE clients
		if updatedCart, err := fe.getCart(context.Background(), userID); err == nil {
			log.WithFields(logrus.Fields{
				"user_id":          userID,
				"cart_items_count": len(updatedCart),
			}).Info("[Suggested Recipe] sending cart notification")
			fe.notifyCartUpdate(userID, updatedCart)
		} else {
			log.WithError(err).WithField("user_id", userID).Error("[Suggested Recipe] failed to get cart for notification")
		}
	}()

	// Redirect back to the suggested recipe detail page with success flag
	http.Redirect(w, r, fmt.Sprintf("%s/suggested-recipe/%s?added=true", baseUrl, id), http.StatusFound)
}
