from typing import Optional
from dataclasses import dataclass, field
from ..protocol import Skill


@dataclass
class SkillRegistry:
    """Skills策略注册器"""
    _skills: dict[str, Skill] = field(default_factory=dict)
    _tool_bindings: dict[str, list[str]] = field(default_factory=dict)
    
    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        
        if skill.bound_tool_name:
            if skill.bound_tool_name not in self._tool_bindings:
                self._tool_bindings[skill.bound_tool_name] = []
            if skill.name not in self._tool_bindings[skill.bound_tool_name]:
                self._tool_bindings[skill.bound_tool_name].append(skill.name)
    
    def unregister(self, name: str) -> bool:
        if name not in self._skills:
            return False
        
        skill = self._skills[name]
        if skill.bound_tool_name and skill.bound_tool_name in self._tool_bindings:
            self._tool_bindings[skill.bound_tool_name] = [
                s for s in self._tool_bindings[skill.bound_tool_name] if s != name
            ]
        
        del self._skills[name]
        return True
    
    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)
    
    def list_all(self) -> list[Skill]:
        return list(self._skills.values())
    
    def get_by_tool(self, tool_name: str) -> list[Skill]:
        skill_names = self._tool_bindings.get(tool_name, [])
        return [self._skills[name] for name in skill_names if name in self._skills]
