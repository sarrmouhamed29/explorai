"""SQL access, always through ``ScopedSessions.scoped`` (``explorai_db.rls``).

Repositories receive a ``ScopedSessions``, never the session factory, so every
transaction runs as ``explorai_app`` under RLS.
"""
