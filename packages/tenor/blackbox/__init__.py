"""Black-box track: ask any meeting-memory product questions through its public MCP and score
what comes back. See docs/method/BLACKBOX.md.

- :mod:`.base`: the :class:`~.base.Product` protocol, the read-only allowlist, the preflight.
- :mod:`.products`: Granola's agent, Concentrate's server, and JSON-specified products.
- :mod:`.transport`: a synchronous MCP client (stdio, Streamable HTTP, in-process).
- :mod:`.kit`: recorded answers, the test-question importer, the committable-report writer.
"""
