class HealthService:
    def __init__(self, registry):
        self.registry = registry

    def status(self):
        return self.registry.snapshot()
