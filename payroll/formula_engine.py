"""
payroll/formula_engine.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #6.

A *safe* expression evaluator for PayslipComponent.formula. Whitelist
only — no attribute access, no calls except a tiny built-in pure-math
set (min, max, abs, round), no name lookup beyond the explicit ctx dict.

This engine is NOT triggered automatically from any save path yet — it's
provisioned for the next pass (the amendment applier and the payroll
posting service will pick it up after the CFO gives the second yes).

Whitelisted names:
    BASIC               Decimal — the employee's basic salary line.
    GROSS               Decimal — running gross.
    PRIOR_LINES_DICT    dict[str, Decimal] — {component_code: amount} for
                                             all lines already on this
                                             payslip.

Whitelisted operations: + - * / // % ** unary +/- comparisons, bool
combinators (and / or / not), parentheses, conditional expressions
(`a if cond else b`), numeric literals, and the safe-funcs above.

Anything else → ValueError.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from typing import Any, Mapping


ZERO = Decimal('0.00')

# Allow these AST node types only.
_ALLOWED_NODES: tuple = (
    ast.Expression, ast.Constant, ast.Num,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call,
    # PRIOR_LINES_DICT["CODE"] lookups — restricted to that one name in
    # _check_ast below.
    ast.Subscript,
)

# Functions the formula author may call. All pure, no side effects.
_SAFE_FUNCS = {
    'min':   min,
    'max':   max,
    'abs':   abs,
    'round': lambda x, n=2: Decimal(x).quantize(Decimal(10) ** -int(n)),
}

_ALLOWED_CTX_KEYS = frozenset({'BASIC', 'GROSS', 'PRIOR_LINES_DICT'})


class FormulaError(ValueError):
    """Raised on any disallowed construct or runtime failure in formula eval."""


def _check_ast(tree: ast.AST) -> None:
    """Walk the AST and reject anything outside the whitelist."""
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(
                f'Disallowed expression node: {type(node).__name__}'
            )
        # Function calls must target a name in _SAFE_FUNCS — no attribute
        # access (foo.bar()) and no indirect calls.
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError('Only direct function calls are allowed.')
            if node.func.id not in _SAFE_FUNCS:
                raise FormulaError(f'Function not allowed: {node.func.id}')
        if isinstance(node, ast.Name):
            if node.id in _SAFE_FUNCS:
                continue
            if node.id not in _ALLOWED_CTX_KEYS:
                raise FormulaError(f'Unknown name: {node.id}')
        # Subscript is only permitted directly on PRIOR_LINES_DICT, e.g.
        # PRIOR_LINES_DICT["TRANSPORT"]. No chained / computed subscripts.
        if isinstance(node, ast.Subscript):
            if not (isinstance(node.value, ast.Name)
                    and node.value.id == 'PRIOR_LINES_DICT'):
                raise FormulaError(
                    'Subscript is only allowed on PRIOR_LINES_DICT.'
                )


class _DecimalizeLiterals(ast.NodeTransformer):
    """Rewrite every numeric literal into ``Decimal("<value>")``.

    Context vars (BASIC, GROSS, …) are promoted to Decimal, but a literal
    like ``0.05`` in the formula is a native Python float, so ``GROSS * 0.05``
    raised ``TypeError: unsupported operand type(s) for *: 'Decimal' and
    'float'``. Converting literals to Decimal keeps the whole computation in
    exact decimal arithmetic. Runs AFTER _check_ast, so the injected Decimal()
    calls are trusted and need no further validation.
    """

    def visit_Constant(self, node: ast.Constant):  # noqa: N802
        if isinstance(node.value, bool):
            return node                                  # leave True/False alone
        if isinstance(node.value, (int, float)):
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id='Decimal', ctx=ast.Load()),
                    args=[ast.Constant(value=str(node.value))],
                    keywords=[],
                ),
                node,
            )
        return node                                      # str keys etc. untouched


def _to_decimal(v: Any) -> Decimal:
    """Coerce a numeric value to Decimal. PRIOR_LINES_DICT remains a dict."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):                              # bool < int — keep as bool
        return Decimal(1) if v else Decimal(0)
    if isinstance(v, (int, float, str)):
        return Decimal(str(v))
    return v                                             # dict / other


def evaluate_formula(formula: str, ctx: Mapping[str, Any]) -> Decimal:
    """Parse, validate, evaluate. Returns Decimal."""
    if not formula or not formula.strip():
        return ZERO

    # Reject any name in ctx that isn't on the whitelist — fail closed.
    for key in ctx:
        if key not in _ALLOWED_CTX_KEYS:
            raise FormulaError(f'Context key not allowed: {key}')

    try:
        tree = ast.parse(formula, mode='eval')
    except SyntaxError as exc:
        raise FormulaError(f'Parse error: {exc}') from exc

    _check_ast(tree)

    # Promote numeric literals to Decimal so e.g. `GROSS * 0.05` stays in
    # exact decimal arithmetic instead of crashing on Decimal * float.
    tree = _DecimalizeLiterals().visit(tree)
    ast.fix_missing_locations(tree)

    # Build the safe globals — only safe funcs + ctx values. __builtins__ off.
    # Decimal is exposed for the literal-rewriting above (authors can't call it
    # directly — _check_ast rejects any bare `Decimal(...)` in the source).
    safe_globals: dict = {'__builtins__': {}, 'Decimal': Decimal}
    safe_globals.update(_SAFE_FUNCS)
    # Promote primitives to Decimal so arithmetic stays exact.
    safe_locals: dict = {}
    for k in _ALLOWED_CTX_KEYS:
        if k in ctx:
            safe_locals[k] = (
                ctx[k] if k == 'PRIOR_LINES_DICT' else _to_decimal(ctx[k])
            )
        else:
            safe_locals[k] = {} if k == 'PRIOR_LINES_DICT' else ZERO

    try:
        result = eval(                                   # noqa: S307 — sandboxed
            compile(tree, '<formula>', 'eval'),
            safe_globals,
            safe_locals,
        )
    except FormulaError:
        raise
    except Exception as exc:                             # noqa: BLE001
        raise FormulaError(f'Evaluation error: {exc}') from exc

    return _to_decimal(result)
