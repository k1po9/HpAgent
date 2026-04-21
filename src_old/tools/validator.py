from typing import Any, Optional


class ValidationError(Exception):
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


class ParamValidator:
    """参数校验模块"""
    
    @staticmethod
    def validate(params: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, list[str]]:
        errors = []
        
        required_fields = schema.get("required", [])
        properties = schema.get("properties", {})
        
        for field in required_fields:
            if field not in params:
                errors.append(f"Missing required field: {field}")
        
        for field_name, field_schema in properties.items():
            if field_name in params:
                value = params[field_name]
                field_type = field_schema.get("type")
                
                if not ParamValidator._check_type(value, field_type):
                    errors.append(f"Field '{field_name}' must be of type {field_type}")
                
                if "enum" in field_schema:
                    if value not in field_schema["enum"]:
                        errors.append(f"Field '{field_name}' must be one of {field_schema['enum']}")
                
                if field_type == "string" and "minLength" in field_schema:
                    if len(value) < field_schema["minLength"]:
                        errors.append(f"Field '{field_name}' must be at least {field_schema['minLength']} characters")
                
                if field_type == "number" or field_type == "integer":
                    if "minimum" in field_schema and value < field_schema["minimum"]:
                        errors.append(f"Field '{field_name}' must be >= {field_schema['minimum']}")
                    if "maximum" in field_schema and value > field_schema["maximum"]:
                        errors.append(f"Field '{field_name}' must be <= {field_schema['maximum']}")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def _check_type(value: Any, expected_type: str) -> bool:
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        
        expected = type_map.get(expected_type)
        if expected is None:
            return True
        
        return isinstance(value, expected)
