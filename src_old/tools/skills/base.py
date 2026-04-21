from ..protocol import Skill


class BaseSkill(Skill):
    """Skill基类"""
    
    name: str = ""
    description: str = ""
    bound_tool_name: str | None = None
    instructions: str = ""
    constraints: dict = {}
    
    async def apply(self, tool_call: dict) -> dict:
        if self.instructions:
            return {"modified": True, "instructions": self.instructions}
        return {"modified": False}
