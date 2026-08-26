def health_payload(registry) -> dict:
    """Stabilny kontrakt API/UI bez importowania backendu przez frontend."""
    return registry.snapshot()
