"""Repository abstractions.

Every repository is defined as an explicit interface (``typing.Protocol``)
with a single in-memory implementation intended for local development,
tests, and the current single-instance deployment model. In-memory storage is
process-local and not durable across Function App restarts or multiple
instances; production deployments that need durability swap the
implementation behind the same interface (for example an Azure Table
Storage-backed repository) without touching callers.
"""
