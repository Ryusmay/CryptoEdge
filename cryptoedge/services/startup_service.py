class StartupService:
    """Reconciliation jest warunkiem gotowości, nie pobocznym logiem."""

    def __init__(self, execution, health=None):
        self.execution = execution
        self.health = health

    def reconcile(self, positions=()):
        result = self.execution.reconcile(positions)
        if self.health:
            self.health.report("reconciliation", "healthy" if result.in_sync else "degraded")
        return result
