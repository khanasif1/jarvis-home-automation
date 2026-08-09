"""Azure Functions backend for the home assistant voice pipeline.

This package is the entire runtime surface deployed to Azure Functions. It
must never import from ``pi-client`` and must remain independently
installable using only ``azure-backend/requirements.txt``.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
