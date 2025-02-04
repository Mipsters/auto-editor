from __future__ import annotations

import cmath
import math
import random
import sys
from dataclasses import dataclass
from fractions import Fraction
from functools import reduce
from time import sleep
from typing import TYPE_CHECKING

import numpy as np

from auto_editor.analyze import edit_method, mut_remove_small
from auto_editor.utils.func import boolop, mut_margin

if TYPE_CHECKING:
    from typing import Any, Callable, Union

    from numpy.typing import NDArray

    Number = Union[int, float, complex, Fraction]
    Real = Union[int, float, Fraction]
    BoolList = NDArray[np.bool_]
    Env = dict[str, Any]


class MyError(Exception):
    pass


class ClosingError(MyError):
    pass


def display_dtype(dtype: np.dtype) -> str:
    if dtype.kind == "b":
        return "bool"

    if dtype.kind == "i":
        return f"int{dtype.itemsize * 8}"

    if dtype.kind == "u":
        return f"uint{dtype.itemsize * 8}"

    return f"float{dtype.itemsize * 8}"


class Null:
    def __init__(self) -> None:
        pass

    def __eq__(self, obj: object) -> bool:
        return type(obj) is Null

    def __len__(self) -> int:
        return 0

    def __next__(self) -> StopIteration:
        raise StopIteration

    def __getitem__(self, ref: int | slice) -> None:
        raise IndexError

    def __str__(self) -> str:
        return "'()"

    __repr__ = __str__


class PaletObject:
    __slots__ = "attributes"

    def __init__(self, attrs: dict[str, Any]):
        self.attributes = attrs


class Cons:
    __slots__ = ("a", "d")

    def __init__(self, a: Any, d: Any):
        self.a = a
        self.d = d

    def __repr__(self) -> str:
        if type(self.d) not in (Cons, Null):
            return f"(cons {self.a} {self.d})"

        result = f"({display_str(self.a)}"
        tail = self.d
        while type(tail) is Cons:
            if type(tail.d) not in (Cons, Null):
                return f"{result} (cons {tail.a} {tail.d}))"
            result += f" {display_str(tail.a)}"
            tail = tail.d

        return f"{result})"

    def __eq__(self, obj: object) -> bool:
        return type(obj) is Cons and self.a == obj.a and self.d == obj.d

    def __len__(self: Cons | Null) -> int:
        count = 0
        while type(self) is Cons:
            self = self.d
            count += 1
        if type(self) is not Null:
            raise MyError("length expects: list?")
        return count

    def __next__(self) -> Any:
        if type(self.d) is Cons:
            return self.d
        raise StopIteration

    def __iter__(self) -> Any:
        while type(self) is Cons:
            yield self.a
            self = self.d

    def __getitem__(self, ref: object) -> Any:
        if type(ref) is not int and type(ref) is not slice:
            raise MyError(f"ref: not a valid index: {print_str(ref)}")

        if type(ref) is int:
            if ref < 0:
                raise MyError(f"ref: negative index not allowed: {ref}")
            pos = ref
            while pos > 0:
                pos -= 1
                self = self.d
                if type(self) is not Cons:
                    raise MyError(f"ref: index out of range: {ref}")

            return self.a

        assert type(ref) is slice

        lst: Cons | Null = Null()
        steps: int = -1
        i: int = 0

        do_reverse = True
        start, stop, step = ref.start, ref.stop, ref.step
        if start is None:
            start = 0
        if step < 0:
            do_reverse = False
            step = -step

            if stop is None:
                stop = float("inf")
            else:
                start, stop = stop + 1, start

        while type(self) is Cons:
            if i > stop - 1:
                break
            if i >= start:
                steps = (steps + 1) % step
                if steps == 0:
                    lst = Cons(self.a, lst)

            self = self.d
            i += 1

        if not do_reverse:
            return lst

        result: Cons | Null = Null()
        while type(lst) is Cons:
            result = Cons(lst.a, result)
            lst = lst.d
        return result


class Char:
    __slots__ = "val"

    def __init__(self, val: str | int):
        if type(val) is int:
            self.val: str = chr(val)
        else:
            assert type(val) is str and len(val) == 1
            self.val = val

    __str__: Callable[[Char], str] = lambda self: self.val

    def __repr__(self) -> str:
        names = {" ": "space", "\n": "newline", "\t": "tab"}
        return f"#\\{self.val}" if self.val not in names else f"#\\{names[self.val]}"

    def __eq__(self, obj: object) -> bool:
        return type(obj) is Char and self.val == obj.val

    def __radd__(self, obj2: str) -> str:
        return obj2 + self.val


class Symbol:
    __slots__ = ("val", "hash")

    def __init__(self, val: str):
        self.val = val
        self.hash = hash(val)

    __str__: Callable[[Symbol], str] = lambda self: self.val
    __repr__ = __str__

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, obj: object) -> bool:
        return type(obj) is Symbol and self.hash == obj.hash


###############################################################################
#                                                                             #
#  LEXER                                                                      #
#                                                                             #
###############################################################################

METHODS = ("audio", "motion", "pixeldiff", "subtitle", "none", "all")
SEC_UNITS = ("s", "sec", "secs", "second", "seconds")
ID, QUOTE, NUM, BOOL, STR, CHAR = "ID", "QUOTE", "NUM", "BOOL", "STR", "CHAR"
ARR, SEC, DB, PER = "ARR", "SEC", "DB", "PER"
LPAREN, RPAREN, LBRAC, RBRAC, LCUR, RCUR, EOF = "(", ")", "[", "]", "{", "}", "EOF"


class Token:
    __slots__ = ("type", "value")

    def __init__(self, type: str, value: Any):
        self.type = type
        self.value = value

    __str__: Callable[[Token], str] = lambda self: f"(Token {self.type} {self.value})"


class Lexer:
    __slots__ = ("text", "pos", "char")

    def __init__(self, text: str):
        self.text = text
        self.pos: int = 0
        self.char: str | None = self.text[self.pos] if text else None

    def char_is_norm(self) -> bool:
        return self.char is not None and self.char not in '()[]{}"; \t\n\r\x0b\x0c'

    def advance(self) -> None:
        self.pos += 1
        self.char = None if self.pos > len(self.text) - 1 else self.text[self.pos]

    def peek(self) -> str | None:
        peek_pos = self.pos + 1
        return None if peek_pos > len(self.text) - 1 else self.text[peek_pos]

    def skip_whitespace(self) -> None:
        while self.char is not None and self.char in " \t\n\r\x0b\x0c":
            self.advance()

    def string(self) -> str:
        result = ""
        while self.char is not None and self.char != '"':
            if self.char == "\\":
                self.advance()
                if self.char in 'nt"\\':
                    if self.char == "n":
                        result += "\n"
                    if self.char == "t":
                        result += "\t"
                    if self.char == '"':
                        result += '"'
                    if self.char == "\\":
                        result += "\\"
                    self.advance()
                    continue

                if self.char is None:
                    raise MyError("Unexpected EOF while parsing")
                raise MyError(
                    f"Unexpected character {self.char} during escape sequence"
                )
            else:
                result += self.char
            self.advance()

        self.advance()
        return result

    def number(self) -> Token:
        result = ""
        token = NUM

        while self.char is not None and self.char in "+-0123456789./":
            result += self.char
            self.advance()

        unit = ""
        if self.char_is_norm():
            while self.char_is_norm():
                assert self.char is not None
                unit += self.char
                self.advance()

            if unit in SEC_UNITS:
                token = SEC
            elif unit == "dB":
                token = DB
            elif unit == "%":
                token = PER
            elif unit != "i":
                return Token(ID, result + unit)

        try:
            if unit == "i":
                return Token(NUM, complex(result + "j"))
            elif "/" in result:
                val = Fraction(result)
                if val.denominator == 1:
                    return Token(token, val.numerator)
                return Token(token, val)
            elif "." in result:
                return Token(token, float(result))
            else:
                return Token(token, int(result))
        except ValueError:
            return Token(ID, result + unit)

    def hash_literal(self) -> Token:
        if self.char == "\\":
            self.advance()
            if self.char is None:
                raise MyError("Expected a character after #\\")

            char = self.char
            self.advance()
            return Token(CHAR, Char(char))

        result = ""
        while self.char_is_norm():
            assert self.char is not None
            result += self.char
            self.advance()

        if result in ("t", "true"):
            return Token(BOOL, True)

        if result in ("f", "false"):
            return Token(BOOL, False)

        raise MyError(f"Unknown hash literal: {result}")

    def get_next_token(self) -> Token:
        while self.char is not None:
            self.skip_whitespace()
            if self.char is None:
                continue

            if self.char == ";":
                while self.char is not None and self.char != "\n":
                    self.advance()
                continue

            if self.char == '"':
                self.advance()
                return Token(STR, self.string())

            if self.char == "'":
                self.advance()
                return Token(QUOTE, "'")

            if self.char in "(){}[]":
                _par = self.char
                self.advance()
                return Token(_par, _par)

            if self.char in "+-":
                _peek = self.peek()
                if _peek is not None and _peek in "0123456789.":
                    return self.number()

            if self.char in "0123456789.":
                return self.number()

            if self.char == "#":
                self.advance()
                return self.hash_literal()

            result = ""
            has_illegal = False
            while self.char_is_norm():
                result += self.char
                if self.char in "'`|\\":
                    has_illegal = True
                self.advance()

            if has_illegal:
                raise MyError(f"Symbol has illegal character(s): {result}")

            for method in METHODS:
                if result == method or result.startswith(method + ":"):
                    return Token(ARR, result)

            return Token(ID, result)

        return Token(EOF, "EOF")


###############################################################################
#                                                                             #
#  PARSER                                                                     #
#                                                                             #
###############################################################################


class BoolArr:
    __slots__ = "val"

    def __init__(self, val: str):
        self.val = val

    __str__: Callable[[BoolArr], str] = lambda self: f"(boolarr {self.val})"


class Parser:
    def __init__(self, lexer: Lexer):
        self.lexer = lexer
        self.current_token = self.lexer.get_next_token()

    def eat(self, token_type: str) -> None:
        if self.current_token.type != token_type:
            raise MyError(f"Expected {token_type}, got {self.current_token.type}")

        self.current_token = self.lexer.get_next_token()

    def expr(self) -> Any:
        token = self.current_token

        if token.type in (CHAR, NUM, STR, BOOL):
            self.eat(token.type)
            return token.value

        matches = {ID: Symbol, ARR: BoolArr}
        if token.type in matches:
            self.eat(token.type)
            return matches[token.type](token.value)

        if token.type == SEC:
            self.eat(SEC)
            return [Symbol("round"), [Symbol("*"), token.value, Symbol("timebase")]]

        if token.type == DB:
            self.eat(DB)
            return [Symbol("pow"), 10, [Symbol("/"), token.value, 20]]

        if token.type == PER:
            self.eat(PER)
            return [Symbol("/"), token.value, 100.0]

        if token.type == QUOTE:
            self.eat(QUOTE)
            return [Symbol("quote"), self.expr()]

        pars = {LPAREN: RPAREN, LBRAC: RBRAC, LCUR: RCUR}
        if token.type in pars:
            self.eat(token.type)
            closing = pars[token.type]
            childs = []
            while self.current_token.type != closing:
                if self.current_token.type == EOF:
                    raise ClosingError(f"Expected closing '{closing}' before end")
                childs.append(self.expr())

            self.eat(closing)
            return childs

        self.eat(token.type)
        childs = []
        while self.current_token.type not in (RPAREN, RBRAC, RCUR, EOF):
            childs.append(self.expr())
        return childs

    def __str__(self) -> str:
        result = str(self.expr())

        self.lexer.pos = 0
        self.lexer.char = self.lexer.text[0]
        self.current_token = self.lexer.get_next_token()

        return result


###############################################################################
#                                                                             #
#  STANDARD LIBRARY                                                           #
#                                                                             #
###############################################################################


class Contract:
    # Convenient flat contract class
    __slots__ = ("name", "c")

    def __init__(self, name: str, c: Callable[[object], bool]):
        self.name = name
        self.c = c

    def __str__(self) -> str:
        return f"<procedure:{self.name}>"

    __repr__ = __str__

    def __call__(self, v: object) -> bool:
        return self.c(v)


def check_args(
    o: str,
    values: list | tuple,
    arity: tuple[int, int | None],
    types: list[Contract] | None,
) -> None:
    lower, upper = arity
    amount = len(values)
    if upper is not None and lower > upper:
        raise ValueError("lower must be less than upper")
    if lower == upper:
        if len(values) != lower:
            raise MyError(f"{o}: Arity mismatch. Expected {lower}, got {amount}")

    if upper is None and amount < lower:
        raise MyError(f"{o}: Arity mismatch. Expected at least {lower}, got {amount}")
    if upper is not None and (amount > upper or amount < lower):
        raise MyError(
            f"{o}: Arity mismatch. Expected between {lower} and {upper}, got {amount}"
        )

    if types is None:
        return

    for i, val in enumerate(values):
        check = types[-1] if i >= len(types) else types[i]
        if not check(val):
            raise MyError(f"{o} expects: {' '.join([c.name for c in types])}")


any_p = Contract("any_p", lambda v: True)
is_proc = Contract("procedure?", lambda v: isinstance(v, (Proc, Contract)))
is_bool = Contract("bool?", lambda v: type(v) is bool)
is_pair = Contract("pair?", lambda v: type(v) is Cons)
is_null = Contract("null?", lambda v: type(v) is Null)
is_symbol = Contract("symbol?", lambda v: type(v) is Symbol)
is_str = Contract("string?", lambda v: type(v) is str)
is_char = Contract("char?", lambda v: type(v) is Char)
is_iterable = Contract(
    "iterable?",
    lambda v: type(v) in (str, range, Cons, Null)
    or isinstance(v, (list, dict, np.ndarray)),
)
is_sequence = Contract(
    "sequence?",
    lambda v: type(v) in (str, range, Cons, Null) or isinstance(v, (list, np.ndarray)),
)
is_range = Contract("range?", lambda v: type(v) is range)
is_vector = Contract("vector?", lambda v: isinstance(v, list))
is_array = Contract("array?", lambda v: isinstance(v, np.ndarray))
is_boolarr = Contract(
    "bool-array?",
    lambda v: isinstance(v, np.ndarray) and v.dtype.kind == "b",
)
is_num = Contract("number?", lambda v: type(v) in (int, float, Fraction, complex))
is_real = Contract("real?", lambda v: type(v) in (int, float, Fraction))
is_int = Contract("int?", lambda v: type(v) is int)
is_uint = Contract("uint?", lambda v: type(v) is int and v > -1)
is_frac = Contract("fraction?", lambda v: type(v) is Fraction)
is_float = Contract("float?", lambda v: type(v) is float)
is_hash = Contract("hash?", lambda v: isinstance(v, dict))
is_void = Contract("void?", lambda v: v is None)


def raise_(msg: str) -> None:
    raise MyError(msg)


def display_str(val: object) -> str:
    if val is None:
        return "#<void>"
    if val is True:
        return "#t"
    if val is False:
        return "#f"
    if type(val) is Symbol:
        return val.val
    if type(val) is str:
        return val
    if type(val) is Char:
        return f"{val}"
    if type(val) is range:
        return "#<range>"
    if type(val) is complex:
        join = "" if val.imag < 0 else "+"
        return f"{val.real}{join}{val.imag}i"

    if isinstance(val, Fraction):
        return f"{val.numerator}/{val.denominator}"
    if isinstance(val, list):
        if not val:
            return "#()"
        result = f"#({display_str(val[0])}"
        for item in val[1:]:
            result += f" {display_str(item)}"
        return result + ")"
    if isinstance(val, np.ndarray):
        kind = val.dtype.kind
        result = f"(array '{display_dtype(val.dtype)}"
        if kind == "b":
            for item in val:
                result += " 1" if item else " 0"
        else:
            for item in val:
                result += f" {item}"
        return result + ")"

    return f"{val!r}"


def print_str(val: object) -> str:
    if type(val) is str:
        return f'"{val}"'
    if type(val) is Char:
        return f"{val!r}"
    if type(val) is Symbol or type(val) is Cons or isinstance(val, list):
        return f"'{display_str(val)}"

    return display_str(val)


def is_equal(a: object, b: object) -> bool:
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        return np.array_equal(a, b)
    return type(a) == type(b) and a == b


def equal_num(*values: object) -> bool:
    return all(values[0] == val for val in values[1:])


def mul(*vals: Any) -> Number:
    return reduce(lambda a, b: a * b, vals, 1)


def minus(*vals: Number) -> Number:
    if len(vals) == 1:
        return -vals[0]
    return reduce(lambda a, b: a - b, vals)


def div(*vals: Any) -> Number:
    if len(vals) == 1:
        vals = (1, vals[0])

    if not {float, complex}.intersection({type(val) for val in vals}):
        result = reduce(Fraction, vals)
        if result.denominator == 1:
            return result.numerator
        return result
    return reduce(lambda a, b: a / b, vals)


def _sqrt(v: Number) -> Number:
    r = cmath.sqrt(v)
    if r.imag == 0:
        if int(r.real) == r.real:
            return int(r.real)
        return r.real
    return r


def _not(val: Any) -> bool | BoolList:
    if is_boolarr(val):
        return np.logical_not(val)
    if is_bool(val):
        return not val
    raise MyError("not expects: bool? or bool-array?")


def _and(*vals: Any) -> bool | BoolList:
    if is_boolarr(vals[0]):
        check_args("and", vals, (2, None), [is_boolarr])
        return reduce(lambda a, b: boolop(a, b, np.logical_and), vals)
    check_args("and", vals, (1, None), [is_bool])
    return reduce(lambda a, b: a and b, vals)


def _or(*vals: Any) -> bool | BoolList:
    if is_boolarr(vals[0]):
        check_args("or", vals, (2, None), [is_boolarr])
        return reduce(lambda a, b: boolop(a, b, np.logical_or), vals)
    check_args("or", vals, (1, None), [is_bool])
    return reduce(lambda a, b: a or b, vals)


def _xor(*vals: Any) -> bool | BoolList:
    if is_boolarr(vals[0]):
        check_args("xor", vals, (2, None), [is_boolarr])
        return reduce(lambda a, b: boolop(a, b, np.logical_xor), vals)
    check_args("xor", vals, (2, None), [is_bool])
    return reduce(lambda a, b: a ^ b, vals)


def string_append(*vals: str | Char) -> str:
    return reduce(lambda a, b: a + b, vals, "")


def vector_append(*vals: list) -> list:
    return reduce(lambda a, b: a + b, vals, [])


def string_ref(s: str, ref: int) -> Char:
    try:
        return Char(s[ref])
    except IndexError:
        raise MyError(f"string index {ref} is out of range")


def number_to_string(val: Number) -> str:
    if isinstance(val, complex):
        join = "" if val.imag < 0 else "+"
        return f"{val.real}{join}{val.imag}i"
    return f"{val}"


dtype_map = {
    Symbol("bool"): np.bool_,
    Symbol("int8"): np.int8,
    Symbol("int16"): np.int16,
    Symbol("int32"): np.int32,
    Symbol("int64"): np.int64,
    Symbol("uint8"): np.uint8,
    Symbol("uint16"): np.uint16,
    Symbol("uint32"): np.uint32,
    Symbol("uint64"): np.uint64,
    Symbol("float32"): np.float32,
    Symbol("float64"): np.float64,
}


def _dtype_to_np(dtype: Symbol) -> type[np.generic]:
    np_dtype = dtype_map.get(dtype)
    if np_dtype is None:
        raise MyError(f"Invalid array dtype: {dtype}")
    return np_dtype


def array_proc(dtype: Symbol, *vals: Any) -> np.ndarray:
    try:
        return np.array(vals, dtype=_dtype_to_np(dtype))
    except OverflowError:
        raise MyError(f"number too large to be converted to {dtype}")


def make_array(dtype: Symbol, size: int, v: int = 0) -> np.ndarray:
    try:
        return np.array([v] * size, dtype=_dtype_to_np(dtype))
    except OverflowError:
        raise MyError(f"number too large to be converted to {dtype}")


def minclip(oarr: BoolList, _min: int) -> BoolList:
    arr = np.copy(oarr)
    mut_remove_small(arr, _min, replace=1, with_=0)
    return arr


def mincut(oarr: BoolList, _min: int) -> BoolList:
    arr = np.copy(oarr)
    mut_remove_small(arr, _min, replace=0, with_=1)
    return arr


def margin(a: int, b: Any, c: Any = None) -> BoolList:
    if c is None:
        check_args("margin", [a, b], (2, 2), [is_int, is_boolarr])
        oarr = b
        start, end = a, a
    else:
        check_args("margin", [a, b, c], (3, 3), [is_int, is_int, is_boolarr])
        oarr = c
        start, end = a, b

    arr = np.copy(oarr)
    mut_margin(arr, start, end)
    return arr


def _list(*values: Any) -> Cons | Null:
    result: Cons | Null = Null()
    for val in reversed(values):
        result = Cons(val, result)
    return result


# convert nested vectors to nested lists
def deep_list(vec: list) -> Cons | Null:
    result: Cons | Null = Null()
    for val in reversed(vec):
        if isinstance(val, list):
            val = deep_list(val)
        result = Cons(val, result)
    return result


def list_to_vector(val: Cons | Null) -> list:
    result = []
    while type(val) is Cons:
        result.append(val.a)
        val = val.d
    return result


def vec_to_list(values: list) -> Cons | Null:
    result: Cons | Null = Null()
    for val in reversed(values):
        result = Cons(val, result)
    return result


def vector_set(vec: list, pos: int, v: Any) -> None:
    try:
        vec[pos] = v
    except IndexError:
        raise MyError(f"vector-set: Invalid index {pos}")


def vector_extend(vec: list, *more_vecs: list) -> None:
    for more in more_vecs:
        vec.extend(more)


def is_list(val: Any) -> bool:
    while type(val) is Cons:
        val = val.d
    return type(val) is Null


def palet_random(*args: int) -> int | float:
    if not args:
        return random.random()

    if args[0] < 1:
        raise MyError(f"random: arg1 ({args[0]}) must be greater than zero")

    if len(args) == 1:
        return random.randrange(0, args[0])

    if args[0] >= args[1]:
        raise MyError(f"random: arg2 ({args[1]}) must be greater than arg1")
    return random.randrange(args[0], args[1])


def palet_map(proc: Proc, seq: str | list | range | NDArray | Cons | Null) -> Any:
    if type(seq) is str:
        return str(map(proc.proc, seq))
    if isinstance(seq, (list, range)):
        return list(map(proc.proc, seq))

    if isinstance(seq, np.ndarray):
        if proc.arity[0] != 0:
            raise MyError(f"map: procedure must take at least one arg")
        check_args(proc.name, [0], (1, 1), None)
        return proc.proc(seq)

    result: Cons | Null = Null()
    while type(seq) is Cons:
        result = Cons(proc.proc(seq.a), result)
        seq = seq.d
    return result[::-1]


def apply(proc: Proc, seq: str | list | range | Cons | Null) -> Any:
    if isinstance(seq, (Cons, Null)):
        return reduce(proc.proc, list_to_vector(seq))
    return reduce(proc.proc, seq)


def ref(seq: Any, ref: int) -> Any:
    try:
        return Char(seq[ref]) if type(seq) is str else seq[ref]
    except KeyError:
        raise MyError(f"ref: Invalid key: {print_str(ref)}")
    except IndexError:
        raise MyError(f"ref: Invalid index: {print_str(ref)}")


def p_slice(
    seq: str | list | range | NDArray | Cons | Null,
    start: int = 0,
    end: int | None = None,
    step: int = 1,
) -> Any:
    if end is None:
        end = len(seq)

    return seq[start:end:step]


def splice(
    arr: NDArray, v: int, start: int | None = None, end: int | None = None
) -> None:
    arr[start:end] = v


def stream_to_list(s: range) -> Cons | Null:
    result: Cons | Null = Null()
    for item in reversed(s):
        result = Cons(item, result)
    return result


def palet_hash(*args: Any) -> dict:
    result = {}
    if len(args) % 2 == 1:
        raise MyError("hash: number of args must be even")
    for key, item in zip(args[0::2], args[1::2]):
        result[key] = item
    return result


###############################################################################
#                                                                             #
#  ENVIRONMENT                                                                #
#                                                                             #
###############################################################################


@dataclass
class Proc:
    name: str
    proc: Callable
    arity: tuple[int, int | None] = (1, None)
    contracts: list[Any] | None = None

    def __str__(self) -> str:
        return f"#<procedure:{self.name}>"

    __repr__ = __str__

    def __call__(self, *vals: Any) -> Any:
        return self.proc(*vals)


class UserProc:
    """A user-defined procedure."""

    def __init__(self, name: str, env: Env, parms: list, body: list):
        self.parms = list(map(str, parms))
        self.body = body
        self.env = env

        self.name = name
        self.arity = len(parms), len(parms)
        self.contracts = None

    def __str__(self) -> str:
        return f"#<procedure:{self.name}>"

    __repr__ = __str__

    def __call__(self, *args: Any) -> Any:
        self.env.update(zip(self.parms, args))
        return my_eval(self.env, self.body[-1])


class Syntax:
    __slots__ = "syn"

    def __init__(self, syn: Callable[[Env, list], Any]):
        self.syn = syn

    def __call__(self, env: Env, node: list) -> Any:
        return self.syn(env, node)


def check_for_syntax(env: Env, node: list) -> Any:
    name = node[0]
    if len(node) < 2:
        raise MyError(f"{name}: bad syntax")

    if len(node) == 2:
        raise MyError(f"{name}: missing body")

    assert isinstance(node[1], list)
    assert isinstance(node[1][0], list)

    var = node[1][0][0]
    if type(var) is not Symbol:
        raise MyError(f"{name}: binding must be an identifier")
    my_iter = my_eval(env, node[1][0][1])

    if not is_iterable(my_iter):
        if type(my_iter) is int:
            return var, range(my_iter)
        raise MyError(f"{name}: got non-iterable in iter slot")

    return var, my_iter


def syn_lambda(env: Env, node: list) -> Any:
    if not isinstance(node[1], list):
        raise MyError(f"{node[0]}: bad syntax")

    return UserProc("", env, node[1], node[2:])  # parms, body


def syn_define(env: Env, node: list) -> Any:
    if len(node) < 3:
        raise MyError("define: bad syntax")

    if isinstance(node[1], list):
        if not node[1] or type(node[1][0]) is not Symbol:
            raise MyError(f"{node[0]}: bad syntax")

        n = node[1][0].val
        parameters = node[1][1:]
        body = node[2:]
        env[n] = UserProc(n, env, parameters, body)
        return None
    elif type(node[1]) is not Symbol:
        raise MyError(f"{node[0]}: must be an identifier")

    n = node[1].val

    if len(node) > 3:
        raise MyError(f"{node[0]}: bad syntax (multiple expressions after identifier)")

    if (
        isinstance(node[2], list)
        and node[2]
        and type(node[2][0]) is Symbol
        and node[2][0].val in ("lambda", "λ")
    ):
        parameters = node[2][1]
        body = node[2][2:]
        env[n] = UserProc(n, env, parameters, body)
    else:
        env[n] = my_eval(env, node[2])
    return None


def syn_set(env: Env, node: list) -> None:
    if len(node) != 3:
        raise MyError(f"{node[0]}: bad syntax")
    if type(node[1]) is not Symbol:
        raise MyError(f"{node[0]}: must be an identifier")

    name = node[1].val
    if name not in env:
        raise MyError(f"{node[0]}: cannot set variable {name} before definition")
    env[name] = my_eval(env, node[2])
    return None


def syn_for(env: Env, node: list) -> None:
    var, my_iter = check_for_syntax(env, node)

    for item in my_iter:
        env[var.val] = item
        for c in node[2:]:
            my_eval(env, c)

    return None


def syn_for_vec(env: Env, node: list) -> list:
    results = []
    var, my_iter = check_for_syntax(env, node)

    for item in my_iter:
        env[var.val] = item
        results.append([my_eval(env, c) for c in node[2:]][-1])

    return results


def syn_if(env: Env, node: list) -> Any:
    if len(node) != 4:
        raise MyError(f"{node[0]}: bad syntax")
    test_expr = my_eval(env, node[1])

    if type(test_expr) is not bool:
        raise MyError(f"{node[0]}: test-expr arg must be: bool?")

    return my_eval(env, node[2] if test_expr else node[3])


def syn_when(env: Env, node: list) -> Any:
    if len(node) != 3:
        raise MyError(f"{node[0]}: bad syntax")
    test_expr = my_eval(env, node[1])

    if type(test_expr) is not bool:
        raise MyError(f"{node[0]}: test-expr arg must be: bool?")

    return my_eval(env, node[2]) if test_expr else None


def syn_cond(env: Env, node: list) -> Any:
    for test_expr in node[1:]:
        if not isinstance(test_expr, list) or not test_expr:
            raise MyError(f"{node[0]}: bad syntax, clause is not a test-value pair")

        if test_expr[0] == Symbol("else"):
            if len(test_expr) == 1:
                raise MyError(f"{node[0]}: missing expression in else clause")
            test_clause = True
        else:
            test_clause = my_eval(env, test_expr[0])
            if type(test_clause) is not bool:
                raise MyError(f"{node[0]}: test-expr must be: bool?")

        if test_clause:
            if len(test_expr) == 1:
                return True

            for rest_clause in test_expr[1:-1]:
                my_eval(env, rest_clause)
            return my_eval(env, test_expr[-1])

    return None


def syn_quote(env: Env, node: list) -> Any:
    if len(node) != 2:
        raise MyError("quote: bad syntax")

    if isinstance(node[1], list):
        return deep_list(node[1])
    return node[1]


def syn_with_open(env: Env, node: list) -> None:
    if len(node) < 2:
        raise MyError(f"{node[0]}: wrong number of args")
    if len(node[1]) != 2 and len(node[1]) != 3:
        raise MyError(f"{node[0]}: wrong number of args")

    if type(node[1][0]) is not Symbol:
        raise MyError(f"{node[0]}: as must be an identifier")

    file_binding = node[1][0].val

    file_name = my_eval(env, node[1][1])
    if type(file_name) is not str:
        raise MyError(f"{node[0]}: file-name must be string?")

    if len(node[1]) == 3:
        file_mode = my_eval(env, node[1][2])
        if type(file_mode) is not Symbol:
            raise MyError(f"{node[0]}: file-mode must be a symbol?")
        if file_mode.val not in ("w", "r", "a"):
            raise MyError(f"{node[0]}: file-mode must be either: 'w 'r 'a")
    else:
        file_mode = Symbol("w")

    with open(file_name, file_mode.val) as file:
        if file_mode.val == "r":
            open_file = PaletObject(
                {
                    "read": Proc("read", file.read, (0, 0)),
                    "readlines": Proc("readlines", file.readlines, (0, 0)),
                }
            )
        else:
            open_file = PaletObject(
                {"write": Proc("write", file.write, (1, 1), [is_str])}
            )

        env[file_binding] = open_file
        for c in node[2:]:
            my_eval(env, c)

    del env[file_binding]
    return None


def syn_dot(env: Env, node: list) -> Any:
    if len(node) != 3:
        raise MyError(".: not enough args")

    my_obj = my_eval(env, node[1])
    if not isinstance(my_obj, PaletObject):
        raise MyError(f".: expected an object, got {my_obj}")

    if not isinstance(node[2], Symbol):
        raise MyError(".: attribute must be an identifier")
    my_attr = node[2].val

    if my_attr not in my_obj.attributes:
        raise MyError(f".: No such attribute: {my_attr}")

    return my_obj.attributes[my_attr]


def my_eval(env: Env, node: object) -> Any:
    if type(node) is Symbol:
        val = env.get(node.val)
        if val is None:
            raise MyError(f"{node.val} is undefined")
        return val

    if isinstance(node, BoolArr):
        if "@filesetup" not in env:
            raise MyError("Can't use edit methods if there's no input files")
        return edit_method(node.val, env["@filesetup"])

    if isinstance(node, list):
        oper = my_eval(env, node[0])
        if not callable(oper):
            """
            ...No one wants to write (aref a x y) when they could write a[x,y].
            In this particular case there is a way to finesse our way out of the
            problem. If we treat data structures as if they were functions on indexes,
            we could write (a x y) instead, which is even shorter than the Perl form.
            """
            if is_iterable(oper):
                values = [my_eval(env, c) for c in node[1:]]
                return ref(oper, *values)

            raise MyError(f"{oper}, expected procedure")

        if type(oper) is Syntax:
            return oper(env, node)

        values = [my_eval(env, c) for c in node[1:]]
        if type(oper) is Contract:
            check_args(oper.name, values, (1, 1), None)
        else:
            check_args(oper.name, values, oper.arity, oper.contracts)
        return oper(*values)

    return node


def syn_eval(env: Env, v: list) -> Any:
    if len(v) != 2:
        raise MyError(f"eval: Arity mismatch, expected 1, given {len(v) - 1}")

    node = my_eval(env, v[1])
    if isinstance(node, list):
        return my_eval(env, node)
    if isinstance(node, Cons):
        return my_eval(env, list_to_vector(node))

    return node


def my_write(v: Any) -> None:
    try:
        sys.stdout.write(v)
    except UnicodeEncodeError as e:
        raise MyError(e)
    return None


env: Env = {
    # constants
    "true": True,
    "false": False,
    "null": Null(),
    "pi": math.pi,
    # syntax
    "lambda": Syntax(syn_lambda),
    "λ": Syntax(syn_lambda),
    "define": Syntax(syn_define),
    "set!": Syntax(syn_set),
    "quote": Syntax(syn_quote),
    "if": Syntax(syn_if),
    "when": Syntax(syn_when),
    "cond": Syntax(syn_cond),
    ".": Syntax(syn_dot),
    "with-open": Syntax(syn_with_open),
    "eval": Syntax(syn_eval),
    # loops
    "for": Syntax(syn_for),
    "for/vector": Syntax(syn_for_vec),
    # actions
    "begin": Proc("begin", lambda *x: x[-1] if x else None, (0, None)),
    "display": Proc("display", lambda v: my_write(display_str(v)), (1, 1)),
    "displayln": Proc("displayln", lambda v: my_write(display_str(v) + "\n"), (1, 1)),
    "print": Proc("print", lambda v: my_write(print_str(v)), (1, 1)),
    "println": Proc("println", lambda v: my_write(print_str(v) + "\n"), (1, 1)),
    "exit": Proc("exit", sys.exit, (0, None)),
    "error": Proc("error", raise_, (1, 1), [is_str]),
    "sleep": Proc("sleep", sleep, (1, 1), [lambda t: is_int(t) or is_float(t)]),
    # void
    "void?": is_void,
    "void": Proc("void", lambda *v: None, (0, 0)),
    # booleans
    "bool?": is_bool,
    ">": Proc(">", lambda a, b: a > b, (2, 2), [is_real, is_real]),
    ">=": Proc(">=", lambda a, b: a >= b, (2, 2), [is_real, is_real]),
    "<": Proc("<", lambda a, b: a < b, (2, 2), [is_real, is_real]),
    "<=": Proc("<=", lambda a, b: a <= b, (2, 2), [is_real, is_real]),
    "=": Proc("=", equal_num, (1, None), [is_num]),
    "eq?": Proc("eq?", lambda a, b: a is b, (2, 2)),
    "equal?": Proc("equal?", is_equal, (2, 2)),
    "not": Proc("not", _not, (1, 1)),
    "and": Proc("and", _and, (1, None)),
    "or": Proc("or", _or, (1, None)),
    "xor": Proc("xor", _xor, (2, None)),
    # number predicates
    "number?": is_num,
    "real?": is_real,
    "int?": is_int,
    "uint?": is_uint,
    "float?": is_float,
    "fraction?": is_frac,
    "positive?": Proc("positive?", lambda v: v > 0, (1, 1), [is_real]),
    "negative?": Proc("negative?", lambda v: v < 0, (1, 1), [is_real]),
    "zero?": Proc("zero?", lambda v: v == 0, (1, 1), [is_num]),
    # numbers
    "+": Proc("+", lambda *v: sum(v), (0, None), [is_num]),
    "-": Proc("-", minus, (1, None), [is_num]),
    "*": Proc("*", mul, (0, None), [is_num]),
    "/": Proc("/", div, (1, None), [is_num]),
    "add1": Proc("add1", lambda v: v + 1, (1, 1), [is_num]),
    "sub1": Proc("sub1", lambda v: v - 1, (1, 1), [is_num]),
    "sqrt": Proc("sqrt", _sqrt, (1, 1), [is_num]),
    "real-part": Proc("real-part", lambda v: v.real, (1, 1), [is_num]),
    "imag-part": Proc("imag-part", lambda v: v.imag, (1, 1), [is_num]),
    # reals
    "pow": Proc("pow", pow, (2, 2), [is_real]),
    "exp": Proc("exp", math.exp, (1, 1), [is_real]),
    "abs": Proc("abs", abs, (1, 1), [is_real]),
    "ceil": Proc("ceil", math.ceil, (1, 1), [is_real]),
    "floor": Proc("floor", math.floor, (1, 1), [is_real]),
    "round": Proc("round", round, (1, 1), [is_real]),
    "max": Proc("max", lambda *v: max(v), (1, None), [is_real]),
    "min": Proc("min", lambda *v: min(v), (1, None), [is_real]),
    "sin": Proc("sin", math.sin, (1, 1), [is_real]),
    "cos": Proc("cos", math.cos, (1, 1), [is_real]),
    "log": Proc("log", math.log, (1, 2), [is_real, is_real]),
    "tan": Proc("tan", math.tan, (1, 1), [is_real]),
    "mod": Proc("mod", lambda a, b: a % b, (2, 2), [is_int, is_int]),
    "modulo": Proc("modulo", lambda a, b: a % b, (2, 2), [is_int, is_int]),
    "random": Proc("random", palet_random, (0, 2), [is_int]),
    # symbols
    "symbol?": is_symbol,
    "symbol->string": Proc("symbol->string", str, (1, 1), [is_symbol]),
    "string->symbol": Proc("string->symbol", Symbol, (1, 1), [is_str]),
    # strings
    "string?": is_str,
    "char?": is_char,
    "string": Proc("string", string_append, (0, None), [is_char]),
    "string-append": Proc("string-append", string_append, (0, None), [is_str]),
    "string-upcase": Proc("string-upcase", str.upper, (1, 1), [is_str]),
    "string-downcase": Proc("string-downcase", str.lower, (1, 1), [is_str]),
    "string-titlecase": Proc("string-titlecase", str.title, (1, 1), [is_str]),
    "char->int": Proc("char->int", lambda c: ord(c.val), (1, 1), [is_char]),
    "int->char": Proc("int->char", Char, (1, 1), [is_int]),
    "~a": Proc("~a", lambda *v: "".join([display_str(a) for a in v]), (0, None)),
    "~s": Proc("~s", lambda *v: " ".join([display_str(a) for a in v]), (0, None)),
    "~v": Proc("~v", lambda *v: " ".join([print_str(a) for a in v]), (0, None)),
    # vectors
    "vector?": is_vector,
    "vector": Proc("vector", lambda *a: list(a), (0, None)),
    "make-vector": Proc(
        "make-vector", lambda size, a=0: [a] * size, (1, 2), [is_uint, any_p]
    ),
    "vector-append": Proc("vector-append", vector_append, (0, None), [is_vector]),
    "vector-pop!": Proc("vector-pop!", list.pop, (1, 1), [is_vector]),
    "vector-add!": Proc("vector-add!", list.append, (2, 2), [is_vector, any_p]),
    "vector-set!": Proc("vector-set!", vector_set, (3, 3), [is_vector, is_int, any_p]),
    "vector-extend!": Proc("vector-extend!", vector_extend, (2, None), [is_vector]),
    # cons/list
    "pair?": is_pair,
    "null?": is_null,
    "cons": Proc("cons", Cons, (2, 2)),
    "car": Proc("car", lambda val: val.a, (1, 1), [is_pair]),
    "cdr": Proc("cdr", lambda val: val.d, (1, 1), [is_pair]),
    "list?": is_list,
    "list": Proc("list", _list, (0, None)),
    "list-ref": Proc("list-ref", ref, (2, 2), [is_pair, is_uint]),
    # arrays
    "array?": is_array,
    "array": Proc("array", array_proc, (2, None), [is_symbol, is_real]),
    "make-array": Proc("make-array", make_array, (2, 3), [is_symbol, is_uint, is_real]),
    "array-splice!": Proc(
        "array-splice!", splice, (2, 4), [is_array, is_real, is_int, is_int]
    ),
    "count-nonzero": Proc("count-nonzero", np.count_nonzero, (1, 1), [is_array]),
    # bool arrays
    "bool-array?": is_boolarr,
    "bool-array": Proc(
        "bool-array", lambda *a: np.array(a, dtype=np.bool_), (1, None), [is_uint]
    ),
    "margin": Proc("margin", margin, (2, 3), None),
    "mincut": Proc("mincut", mincut, (2, 2), [is_int, is_boolarr]),
    "minclip": Proc("minclip", minclip, (2, 2), [is_int, is_boolarr]),
    # ranges
    "range?": is_range,
    "range": Proc("range", range, (1, 3), [is_real, is_real, is_real]),
    # generic iterables
    "iterable?": is_iterable,
    "sequence?": is_sequence,
    "length": Proc("length", len, (1, 1), [is_iterable]),
    "reverse": Proc("reverse", lambda v: v[::-1], (1, 1), [is_sequence]),
    "ref": Proc("ref", ref, (2, 2), [is_iterable, is_int]),
    "slice": Proc("slice", p_slice, (2, 4), [is_sequence, is_int]),
    # procedures
    "procedure?": is_proc,
    "map": Proc("map", palet_map, (2, 2), [is_proc, is_iterable]),
    "apply": Proc("apply", apply, (2, 2), [is_proc, is_iterable]),
    # hashs
    "hash?": is_hash,
    "hash": Proc("hash", palet_hash),
    "has-key?": Proc("has-key?", lambda h, k: k in h, (2, 2), [is_hash, any_p]),
    # conversions
    "number->string": Proc("number->string", number_to_string, (1, 1), [is_num]),
    "string->list": Proc(
        "string->list", lambda s: vec_to_list([Char(c) for c in s]), (1, 1), [is_str]
    ),
    "string->vector": Proc(
        "string->vector", lambda s: [Char(c) for c in s], (1, 1), [is_str]
    ),
    "list->vector": Proc("list->vector", list_to_vector, (1, 1), [is_pair]),
    "vector->list": Proc("vector->list", vec_to_list, (1, 1), [is_vector]),
    "range->list": Proc("range->list", stream_to_list, (1, 1), [is_range]),
    "range->vector": Proc("range->vector", list, (1, 1), [is_range]),
    # predicates
    "any": any_p,
}


###############################################################################
#                                                                             #
#  INTERPRETER                                                                #
#                                                                             #
###############################################################################


class Interpreter:
    __slots__ = ("parser", "env")

    def __init__(self, env: Env, parser: Parser):
        self.parser = parser
        self.env = env

    def interpret(self) -> Any:
        result = []
        while self.parser.current_token.type not in (EOF, RPAREN, RBRAC, RCUR):
            result.append(my_eval(self.env, self.parser.expr()))
        return result
