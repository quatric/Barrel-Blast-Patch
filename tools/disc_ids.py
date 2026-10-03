"""Match disc images by ID4 while retaining canonical patch-data keys."""


def match_disc_id(disc_id, supported):
    """Return a supported ID6 for this ID4, or None for an unknown game.

    Keep an exact key when available so existing variant-specific patch data
    still applies. Never rewrite the image header or its loader filename.
    """
    if len(disc_id) != 6:
        return None
    if disc_id in supported:
        return disc_id
    return next((key for key in supported
                 if len(key) == 6 and key[:4] == disc_id[:4]), None)
