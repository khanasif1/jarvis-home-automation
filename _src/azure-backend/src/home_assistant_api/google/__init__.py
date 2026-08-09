"""Google integration adapters (OAuth, credential storage, API clients).

All Google integration is optional. Every adapter fails explicitly with
:class:`~home_assistant_api.errors.ConfigurationError` when the required
client configuration or a stored credential is missing -- there is no silent
no-op path.
"""
