from typing import Any
from ..registry import Tool


class CalculatorTool(Tool):
    name: str = "calculator"
    description: str = "Perform basic arithmetic calculations. Use this when you need to compute mathematical expressions."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate (e.g., '2 + 2', '10 * 5', '100 / 4')"
            }
        },
        "required": ["expression"]
    }

    async def execute(self, expression: str) -> str:
        try:
            allowed_chars = set("0123456789+-*/.() ")
            if not all(c in allowed_chars for c in expression):
                return f"Error: Invalid characters in expression"
            
            result = eval(expression)
            return f"{expression} = {result}"
        except ZeroDivisionError:
            return "Error: Division by zero"
        except Exception as e:
            return f"Error: {str(e)}"
