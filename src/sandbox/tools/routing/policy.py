from .models import RegisteredTool, ScoredCandidate, ToolExposure


class CandidatePolicy:
    @staticmethod
    def select_always(tools):
        return [t.name for t in tools if t.routing.exposure is ToolExposure.ALWAYS]

    @staticmethod
    def select_front_doors(tools):
        families: dict[str, RegisteredTool] = {}
        for tool in tools:
            if tool.routing.exposure is not ToolExposure.FRONT_DOOR:
                continue
            family = tool.routing.front_door_family or tool.name
            current = families.get(family)
            if current is None or tool.routing.front_door_priority > current.routing.front_door_priority:
                families[family] = tool
        return [tool.name for _, tool in sorted(families.items())]

    @staticmethod
    def merge(*, always: list[str], front_doors: list[str], semantic: tuple[ScoredCandidate, ...], final_limit: int) -> list[str]:
        if len(always) > final_limit:
            raise ValueError("always_if_eligible tools exceed final tool limit")
        result: list[str] = []
        for name in [*always, *front_doors, *(c.tool_name for c in semantic)]:
            if name not in result and len(result) < final_limit:
                result.append(name)
        return result
