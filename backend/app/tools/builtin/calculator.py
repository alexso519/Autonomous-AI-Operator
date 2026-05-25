import ast
import operator
from typing import Any

from app.tools.tool_models import CalculatorInput
from app.tools.tool_models import ToolExecutionError

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


def _evaluate_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ToolExecutionError("Unsupported constant type in calculator expression.")
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        op = _ALLOWED_OPERATORS.get(type(node.op))
        if op is None:
            raise ToolExecutionError("Unsupported operator in calculator expression.")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand)
        op = _ALLOWED_OPERATORS.get(type(node.op))
        if op is None:
            raise ToolExecutionError("Unsupported unary operator in calculator expression.")
        return op(operand)
    raise ToolExecutionError("Unsupported syntax in calculator expression.")


def calculate_expression(input_model: CalculatorInput) -> dict[str, Any]:
    expression = input_model.expression.strip()
    if not expression:
        raise ToolExecutionError("Calculator expression cannot be empty.")

    try:
        parsed = ast.parse(expression, mode="eval")
        result = _evaluate_node(parsed.body)
    except Exception as exc:
        raise ToolExecutionError(f"Calculator evaluation failed: {exc}") from exc

    formatted = str(result)
    return {
        "result": result,
        "formatted": formatted,
    }
