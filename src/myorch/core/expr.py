"""A tiny, safe expression language for `when`, `until` and approval counts.

    steps.verify.outputs.blocking > 0
    steps.ci.outputs.passed == true and loop.round < 3
    max(steps.risk-rules.outputs.approvers, steps.classify-risk.outputs.approvers)

Grammar (no arithmetic, so '-' is an identifier character and step ids can be used as-is):

    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := atom (("=="|"!="|"<"|"<="|">"|">=") atom)?
    atom    := number | string | true | false | null | call | ref | "(" expr ")"
    call    := name "(" [expr ("," expr)*] ")"        # max, min, len
    ref     := name ("." name)*

References resolve against a nested mapping; anything missing evaluates to None. Ordering
comparisons involving None are False, so a missing output never satisfies `> 0`.
Pure and deterministic, so it is safe to evaluate inside workflow code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>\d+(?:\.\d+)?)
      | (?P<str>'[^']*'|"[^"]*")
      | (?P<op>==|!=|<=|>=|<|>|\(|\)|,)
      | (?P<name>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)
    )""",
    re.VERBOSE,
)
_FUNCS = {
    "max": lambda *a: max((x for x in a if x is not None), default=None),
    "min": lambda *a: min((x for x in a if x is not None), default=None),
    "len": lambda x: len(x) if x is not None else 0,
}


class ExprError(ValueError):
    pass


def _tokenize(src: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    src = src.strip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise ExprError(f"unexpected character at {pos} in {src!r}")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.toks = _tokenize(src)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok is None or (value is not None and tok[1] != value):
            raise ExprError(f"expected {value or 'token'} in {self.src!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.peek() is not None:
            raise ExprError(f"unexpected {self.peek()[1]!r} in {self.src!r}")
        return node

    def or_(self):
        node = self.and_()
        while self.peek() == ("name", "or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek() == ("name", "and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek() == ("name", "not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.atom()
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            return ("cmp", tok[1], left, self.atom())
        return left

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return ("lit", float(val) if "." in val else int(val))
        if kind == "str":
            return ("lit", val[1:-1])
        if kind == "op" and val == "(":
            node = self.or_()
            self.take(")")
            return node
        if kind == "name":
            if val in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[val])
            if self.peek() == ("op", "("):
                if val not in _FUNCS:
                    raise ExprError(f"unknown function {val!r}")
                self.take("(")
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.or_())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.or_())
                self.take(")")
                return ("call", val, args)
            return ("ref", val.split("."))
        raise ExprError(f"unexpected {val!r} in {self.src!r}")


def _resolve(path: list[str], ctx: Mapping[str, Any]) -> Any:
    cur: Any = ctx
    for part in path:
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _eval(node, ctx):
    op = node[0]
    if op == "lit":
        return node[1]
    if op == "ref":
        return _resolve(node[1], ctx)
    if op == "not":
        return not _eval(node[1], ctx)
    if op == "and":
        return bool(_eval(node[1], ctx)) and bool(_eval(node[2], ctx))
    if op == "or":
        return bool(_eval(node[1], ctx)) or bool(_eval(node[2], ctx))
    if op == "call":
        return _FUNCS[node[1]](*[_eval(a, ctx) for a in node[2]])
    if op == "cmp":
        cmp, a, b = node[1], _eval(node[2], ctx), _eval(node[3], ctx)
        if cmp == "==":
            return a == b
        if cmp == "!=":
            return a != b
        if a is None or b is None:
            return False
        try:
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[cmp]
        except TypeError:
            return False
    raise ExprError(f"bad node {op}")


def compile_expr(src: str):
    """Parse once (raises ExprError on bad syntax); returns a callable ctx -> value."""
    tree = _Parser(src).parse()
    return lambda ctx: _eval(tree, ctx)


def evaluate(src: str, ctx: Mapping[str, Any]) -> Any:
    return compile_expr(src)(ctx)


def truthy(src: str, ctx: Mapping[str, Any]) -> bool:
    return bool(evaluate(src, ctx))
