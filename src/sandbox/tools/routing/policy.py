from .models import (
    RegisteredTool, ScoredCandidate, ToolExposure,
    ToolRoutingConfigurationError,
)


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
            if current is None or (
                (-tool.routing.front_door_priority, tool.name)
                < (-current.routing.front_door_priority, current.name)
            ):
                families[family] = tool
        selected = families.items()
        return [
            tool.name for family, tool in sorted(
                selected,
                key=lambda item: (-item[1].routing.front_door_priority, item[0], item[1].name),
            )
        ]

    @staticmethod
    def merge(*, always: list[str], front_doors: list[str], semantic: tuple[ScoredCandidate, ...], final_limit: int) -> list[str]:
        reserved = list(dict.fromkeys([*always, *front_doors]))
        if len(reserved) > final_limit:
            raise ToolRoutingConfigurationError(
                "always/front-door reserved tools exceed final tool limit"
            )
        result = reserved.copy()
        for name in (c.tool_name for c in semantic):
            if name not in result and len(result) < final_limit:
                result.append(name)
        return result
