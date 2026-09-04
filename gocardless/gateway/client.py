# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""Isolated GoCardless SDK client construction, one client per settings record."""

import gocardless_pro


def get_client(access_token: str, environment: str) -> gocardless_pro.Client:
	"""Build a GoCardless SDK client for one settings record.

	The client is always constructed from the caller's own credentials and never
	stored on a module-level global, so concurrent requests for different
	merchant accounts cannot cross credentials. The SDK constructor performs no
	network call, so this is safe to invoke during document validation.
	"""
	return gocardless_pro.Client(access_token=access_token, environment=environment)
