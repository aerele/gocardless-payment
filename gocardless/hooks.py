app_name = "gocardless"
app_title = "GoCardless Payment"
app_publisher = "Aerele"
app_description = "GoCardless payment gateway integration for Frappe and ERPNext, built on Payment Core"
app_email = "hello@aerele.in"
app_license = "mit"

# Apps
# ------------------

# payment_core provides the shared payment contracts this app is built on
# (GatewayControllerMixin, gateway registration and reference guards).
# erpnext is required transitively by payment_core.
required_apps = ["payment_core"]

# Scope 1: one-off payment flow
# ------------------------------

# Scope 2: webhook and settlement flow
# -------------------------------------
# Keep the cached webhook secrets in sync with the settings records so rotated
# secrets take effect immediately. (The payments app defined this cache clear
# but never wired it up.)
doc_events = {
	"GoCardless Settings": {
		"on_update": "gocardless.gateway.webhooks.clear_cache",
		"on_trash": "gocardless.gateway.webhooks.clear_cache",
	}
}

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True
