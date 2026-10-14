"""
Remote control of other nodes: the identity every node owns, the pinned mutual TLS between a server and its clients,
discovery and pairing of unpaired clients, and the transfer of playlists and uploads when a client joins or leaves.

Everything in here that does not need the running manager (identity, TLS contexts, announcement parsing, the rules of
the transfer) is plain functions on plain data, so it can be tested without a node.
"""
