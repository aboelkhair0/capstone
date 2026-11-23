import re
import signal
from typing import Optional

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )
    SYMPY_AVAILABLE = True
    _TRANSFORMS = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
    )
except Exception:
    SYMPY_AVAILABLE = False
    sp = None
    _TRANSFORMS = ()


def _balance_parens(expr: str) -> str:
    """Balances unmatched parentheses (common when OCR misses one faint closing bracket)."""
    opens = expr.count("(")
    closes = expr.count(")")
    if opens > closes:
        expr = expr + ")" * (opens - closes)
    elif closes > opens:
        expr = "(" * (closes - opens) + expr
    return expr


def _latex_to_sympy_str(raw: str) -> str:
    """Converts common handwritten/LaTeX math strings into SymPy-parseable text."""
    s = raw.strip()
    s = s.replace("$$", "").replace("$", "")
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = s.replace("π", "pi").replace(r"\pi", "pi")
    s = s.replace("·", "*").replace("×", "*").replace(r"\cdot", "*").replace(r"\times", "*")
    s = s.replace("−", "-")
    # Convert \frac{a}{b} -> ((a)/(b))
    for _ in range(4):
        new_s = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"((\1)/(\2))", s)
        if new_s == s:
            break
        s = new_s
    # Common LaTeX functions
    for fn in ("sin", "cos", "tan", "sec", "csc", "cot", "ln", "log", "exp", "sqrt"):
        s = re.sub(rf"\\{fn}\b", fn, s)
    # Replace remaining curly braces { ... } with parentheses ( ... )
    s = s.replace("{", "(").replace("}", ")")
    # Clean spaces around ^ and function calls like "sin x" -> "sin(x)"
    s = re.sub(r"\s*\^\s*", "^", s)
    s = re.sub(r"\b(sin|cos|tan|sec|csc|cot|ln|log|sqrt)\s+([a-zA-Z0-9]+)\b", r"\1(\2)", s)
    # Fix "tan(x^2 sin(x))^..." spacing
    s = re.sub(r"\s+", " ", s).strip()
    return _balance_parens(s)


def _safe_parse(expr_str: str, local_syms: dict):
    if not SYMPY_AVAILABLE:
        return None
    cleaned = _latex_to_sympy_str(expr_str)
    if not cleaned or len(cleaned) > 160:
        return None
    try:
        return parse_expr(cleaned, local_dict=local_syms, transformations=_TRANSFORMS)
    except Exception:
        return None


def _check_wave_equation(text: str) -> Optional[str]:
    """Extracts exact amplitude, angular frequency, frequency, and period from [y(t) =] A sin(w*t + phi)."""
    low = text.lower()
    m = re.search(
        r"(?:y\s*\(\s*t\s*\)\s*=\s*)?([0-9.]+)?\s*\*?\s*(?:\\?sin|\\?cos)\s*\(\s*([^)]+)\)",
        text,
        re.IGNORECASE,
    )
    if not m or not SYMPY_AVAILABLE:
        return None
    # If y(t)= wasn't written, only trigger wave analysis when the question mentions wave terms
    if "y(" not in low.replace(" ", "") and not any(k in low for k in ("amplitude", "frequency", "period", "wave", "angular")):
        return None
    amp_str = m.group(1) or "1"
    inside = m.group(2)
    t = sp.Symbol("t", real=True)
    local_syms = {"t": t, "pi": sp.pi, "sin": sp.sin, "cos": sp.cos}
    expr = _safe_parse(inside, local_syms)
    if expr is None:
        return None
    try:
        omega = sp.simplify(sp.diff(expr, t))
        if omega == 0 or omega.has(t):
            return None
        omega_abs = sp.Abs(omega)
        freq = sp.simplify(omega_abs / (2 * sp.pi))
        period = sp.simplify(2 * sp.pi / omega_abs)
        return (
            f"Wave equation y(t): Amplitude A = {amp_str}, angular frequency omega = {omega_abs} rad/s, "
            f"frequency f = omega/(2*pi) = {freq} Hz, period T = 1/f = {period} s."
        )
    except Exception:
        return None


def _check_dilution(text: str) -> Optional[str]:
    """Computes exact M1*V1 = M2*V2 when volume and two molarities are stated."""
    low = text.lower()
    if not any(k in low for k in ("molar", "stock", "dilut", "prepare", "solution", " m ")):
        return None
    m_vol = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l)\b", low)
    molarities = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*[Mm]\b", text)]
    if not m_vol or len(molarities) < 2:
        return None
    v2 = float(m_vol.group(1))
    unit = "mL" if m_vol.group(2) == "ml" else "L"
    # Higher molarity is typically the concentrated stock (M1), lower is target (M2)
    m1 = max(molarities[:2])
    m2 = min(molarities[:2])
    if m1 <= 0 or m2 <= 0 or m1 == m2:
        return None
    v1 = (m2 * v2) / m1
    water = v2 - v1
    return (
        f"Dilution check (M1*V1 = M2*V2): M1 = {m1:g} M (stock), M2 = {m2:g} M (target), "
        f"V2 = {v2:g} {unit} -> V1 = ({m2:g} * {v2:g}) / {m1:g} = {v1:g} {unit} of stock "
        f"(plus ~{water:g} {unit} distilled water to reach {v2:g} {unit} total volume)."
    )


def _check_derivative(text: str) -> Optional[str]:
    """Computes exact symbolic derivative for d/dx (...) or 'derivative of ...'."""
    if not SYMPY_AVAILABLE:
        return None
    # Match LaTeX \frac{d}{dx} (...) or d/dx (...)
    expr_part = None
    m = re.search(r"(?:\\frac\s*\{\s*d\s*\}\s*\{\s*d\s*([a-zA-Z])\s*\}|d\s*/\s*d([a-zA-Z]))\s*(.+)", text)
    var_name = "x"
    if m:
        var_name = (m.group(1) or m.group(2) or "x").lower()
        expr_part = m.group(3).strip()
    else:
        m2 = re.search(r"derivative\s+of\s+([^\n?.]+)", text, re.IGNORECASE)
        if m2:
            expr_part = m2.group(1).strip()

    if not expr_part:
        return None

    # Clean trailing prompt words if user typed them after the expression
    expr_part = re.split(r"\b(with respect to|step by step|briefly|please)\b", expr_part, flags=re.IGNORECASE)[0].strip()
    x = sp.Symbol(var_name)
    local_syms = {
        var_name: x,
        "x": sp.Symbol("x"),
        "t": sp.Symbol("t"),
        "pi": sp.pi,
        "e": sp.E,
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "sec": sp.sec,
        "csc": sp.csc,
        "cot": sp.cot,
        "ln": sp.log,
        "log": sp.log,
        "exp": sp.exp,
        "sqrt": sp.sqrt,
    }
    expr = _safe_parse(expr_part, local_syms)
    if expr is None or not expr.has(x):
        return None

    try:
        deriv = sp.diff(expr, x)
        method_hint = ""
        # Detect variable in both base and exponent -> logarithmic differentiation
        if any(isinstance(node, sp.Pow) and node.base.has(x) and node.exp.has(x) for node in sp.preorder_traversal(expr)):
            method_hint = "Method: logarithmic differentiation (since x is in both base and exponent). "
        deriv_str = str(deriv)
        if len(deriv_str) > 220:
            deriv_str = deriv_str[:220] + "..."
        return f"Symbolic derivative d/d{var_name}({expr}): {method_hint}Exact derivative = {deriv_str}"
    except Exception:
        return None


def verify_math_expression(question: str, camera_ocr: str = "") -> str:
    """Runs fast CPU-only symbolic math verification (<0.05s, 0 MB GPU VRAM).

    Returns a short verified math hint string for the LLM prompt, or '' if not applicable.
    """
    if not SYMPY_AVAILABLE:
        return ""
    combined = f"{camera_ocr}\n{question}".strip()
    if not combined:
        return ""

    # Run fast pattern checks in priority order
    for fn in (_check_wave_equation, _check_dilution, _check_derivative):
        try:
            res = fn(combined)
            if res:
                return res
        except Exception:
            continue
    return ""


if __name__ == "__main__":
    samples = [
        ("What are the amplitude, frequency, and period of y(t) = 4 sin(2*pi*t + pi)?", ""),
        ("How do you prepare 250 mL of 0.5 M NaCl from a 2.0 M stock solution?", ""),
        ("Solve this problem.", r"$$\frac { d } { d x } ( \tan ( x ^ { 2 } \sin x ) ^ { x ^ { 2 } - x }$$"),
    ]
    for q, cam in samples:
        print("Q:", q)
        print("->", verify_math_expression(q, cam) or "(no match)")
        print()
