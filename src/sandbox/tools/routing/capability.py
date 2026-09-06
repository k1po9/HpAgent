from .models import EligibilityDecision, RegisteredTool, ResourceScope, RuntimeCapabilitySnapshot, ToolExposure


class CapabilityMatcher:
    def evaluate(self, tool: RegisteredTool, runtime: RuntimeCapabilitySnapshot) -> EligibilityDecision:
        spec = tool.routing
        if spec.exposure is ToolExposure.INTERNAL:
            return EligibilityDecision(False, "internal_tool")
        if spec.requires_workspace and not runtime.has_workspace:
            return EligibilityDecision(False, "workspace_unavailable")
        if spec.requires_run_file_scope and not runtime.has_run_file_scope:
            return EligibilityDecision(False, "run_file_scope_unavailable")
        compatible = tuple(r for r in runtime.resources if r.scope is spec.resource_scope)
        if spec.accepts_directions:
            compatible = tuple(r for r in compatible if (r.direction or "").lower() in spec.accepts_directions)
            if not compatible:
                return EligibilityDecision(False, "no_compatible_direction")
        if spec.accepts_extensions:
            compatible = tuple(r for r in compatible if (r.extension or "").lower().lstrip(".") in spec.accepts_extensions)
            if not compatible:
                return EligibilityDecision(False, "no_compatible_extension")
        if spec.accepts_media_types:
            compatible = tuple(r for r in compatible if (r.media_type or "").lower() in spec.accepts_media_types)
            if not compatible:
                return EligibilityDecision(False, "no_compatible_media_type")
        if not spec.required_services.issubset(runtime.available_services):
            return EligibilityDecision(False, "required_service_unavailable")
        return EligibilityDecision(True)
