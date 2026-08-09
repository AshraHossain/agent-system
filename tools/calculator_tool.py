def calculator_tool(expression: str) -> str:
    try:
        return str(eval(expression))
    except Exception:
        return "Error in calculation"
