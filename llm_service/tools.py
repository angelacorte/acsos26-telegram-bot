"""Sandboxed utilities the tool-calling agent may invoke.

Both helpers are deliberately hardened: they parse bounded input and never
execute arbitrary Python, touch the filesystem or network, or run shell
commands. `safe_calculate` evaluates arithmetic via a restricted AST walk;
`analyze_structured_data` counts and aggregates bounded JSON/CSV.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
from typing import Any

MAX_TOOL_INPUT_CHARS = 50_000
MAX_TOOL_ROWS = 2_000


def safe_calculate(expression: str) -> str:
    """Evaluate bounded arithmetic without Python eval, names, calls, or attribute access."""
    if len(expression) > 500:
        return "Error: expression is too long (maximum 500 characters)."
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        return "Error: invalid arithmetic expression."

    def evaluate(node: ast.AST, depth: int = 0) -> int | float:
        if depth > 30:
            raise ValueError("expression is too deeply nested")
        if isinstance(node, ast.Expression):
            return evaluate(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = evaluate(node.operand, depth + 1)
            value = operand if isinstance(node.op, ast.UAdd) else -operand
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
        ):
            left = evaluate(node.left, depth + 1)
            right = evaluate(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 12 or abs(left) > 1_000_000:
                    raise ValueError("power is outside the safe limit")
                value = left**right
            elif isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                value = left / right
            elif isinstance(node.op, ast.FloorDiv):
                value = left // right
            else:
                value = left % right
        else:
            raise ValueError("only numeric arithmetic is allowed")
        if isinstance(value, complex) or not math.isfinite(float(value)) or abs(value) > 1e100:
            raise ValueError("result is outside the safe limit")
        return value

    try:
        result = evaluate(tree)
    except (ArithmeticError, OverflowError, ValueError) as error:
        return f"Error: {error}."
    return str(result)


def analyze_structured_data(data: str, operation: str = "count", field: str = "") -> str:
    """Parse bounded JSON or CSV and count, inspect, or aggregate it without executing code."""
    if len(data) > MAX_TOOL_INPUT_CHARS:
        return f"Error: input is too large (maximum {MAX_TOOL_INPUT_CHARS} characters)."
    try:
        stripped = data.strip()
        if stripped.startswith(("[", "{")):
            parsed = json.loads(stripped)
            rows = parsed if isinstance(parsed, list) else [parsed]
        else:
            rows = list(csv.DictReader(io.StringIO(data)))
    except (csv.Error, json.JSONDecodeError, RecursionError, UnicodeError) as error:
        return f"Error: could not parse structured data: {error}."
    if len(rows) > MAX_TOOL_ROWS:
        return f"Error: too many rows (maximum {MAX_TOOL_ROWS})."

    normalized_operation = operation.strip().casefold()
    if normalized_operation == "count":
        return str(len(rows))
    if normalized_operation == "fields":
        fields = sorted({str(key) for row in rows if isinstance(row, dict) for key in row})
        return json.dumps(fields, ensure_ascii=False)
    if not field:
        return "Error: field is required for this operation."

    def field_value(row: Any) -> Any:
        value = row
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(field)
            value = value[part]
        return value

    try:
        values = [field_value(row) for row in rows]
    except KeyError:
        return f"Error: field {field!r} is missing from at least one row."
    if normalized_operation == "unique":
        unique = list(dict.fromkeys(json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values))
        return json.dumps([json.loads(value) for value in unique], ensure_ascii=False)
    if normalized_operation not in {"sum", "min", "max", "average"}:
        return "Error: operation must be count, fields, unique, sum, min, max, or average."
    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError):
        return f"Error: field {field!r} contains non-numeric values."
    if not numbers:
        return "Error: there are no values to aggregate."
    if not all(math.isfinite(value) for value in numbers):
        return "Error: numeric values must be finite."
    result = {
        "sum": sum,
        "min": min,
        "max": max,
        "average": lambda items: sum(items) / len(items),
    }[normalized_operation](numbers)
    return str(result)
