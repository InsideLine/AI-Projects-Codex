from __future__ import annotations

from .analysis import RuleEngine
from .models import InvestigationInput, InvestigationReport


class LicenseViolationAgent:
    """Application service for report generation."""

    def __init__(self, rule_engine: RuleEngine | None = None) -> None:
        self.rule_engine = rule_engine or RuleEngine()

    def create_report(self, investigation: InvestigationInput) -> InvestigationReport:
        return self.rule_engine.analyze(investigation)

