import ast
import operator
import re

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_ARITHMETIC_PATTERN = re.compile(r"-?\d+(?:\.\d+)?\s*[-+*/%]\s*-?\d+(?:\.\d+)?")


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


def _extract_arithmetic(text: str) -> str:
    """Extract arithmetic expression from prose text.

    If text parses as valid Python arithmetic, return it as-is.
    Otherwise, search for arithmetic patterns and extract the first match.
    """
    text = text.strip()
    try:
        ast.parse(text, mode="eval")
        return text
    except SyntaxError:
        match = _ARITHMETIC_PATTERN.search(text)
        if match:
            return match.group(0)
        return text


def calculator_tool(expression: str) -> str:
    """Evaluate a numeric expression without executing arbitrary code.

    Intelligently extracts arithmetic from prose (e.g., "Compute 3 + 4" → "3 + 4").
    Supports nested expressions like "(2 + 3) * 4" if they parse as valid Python.
    """
    try:
        arithmetic_expr = _extract_arithmetic(expression)
        tree = ast.parse(arithmetic_expr, mode="eval")
        return str(_eval_node(tree.body))
    except Exception:
        return "Error in calculation"
