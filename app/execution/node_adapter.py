"""Let the sandbox call tree and linked-list solutions with plain lists.

A LeetCode-style solution takes a `TreeNode` or a `ListNode`; a test case can
only carry JSON. Without a bridge no suite could ever be built for such code,
so every tree problem ended "inconclusive" (measured: Binary Tree Maximum Path
Sum, `0/0`).

`adapt_nodes` wraps the submission's node-taking functions so they can be
called the way a test case is written:

- a binary tree as its LeetCode level-order list, `None` for a missing child
  (`[1, 2, 3, None, 4]`); the empty tree is `[]`,
- a linked list as a plain list; the empty list is `[]`,
- a returned tree or list is turned back into the same form.

It is applied host-side to the code about to be SENT to the sandbox
(`SandboxRunner.run`), never to what the learner or the model is shown. It is
text generation over the parsed source (`ast`): nothing here executes, imports
or evaluates the submission, which stays untrusted. Code with no node
parameter, and anything that does not parse, is returned unchanged.
"""

import ast
from typing import Final, Literal

__all__ = ["adapt_nodes"]

NodeKind = Literal["tree", "list"]

_TREE_NAMES: Final = frozenset({"root", "tree", "root1", "root2", "subRoot", "subroot", "p", "q"})
_LIST_NAMES: Final = frozenset({"head", "l1", "l2", "list1", "list2", "headA", "headB"})
_TREE_ATTRS: Final = frozenset({"left", "right"})
_LIST_ATTRS: Final = frozenset({"next"})

_TREE_CLASS: Final = (
    "class TreeNode:\n"
    "    def __init__(self, val=0, left=None, right=None):\n"
    "        self.val = val\n"
    "        self.left = left\n"
    "        self.right = right\n"
)
_LIST_CLASS: Final = (
    "class ListNode:\n"
    "    def __init__(self, val=0, next=None):\n"
    "        self.val = val\n"
    "        self.next = next\n"
)

#: Converters. `_aca_depth` makes only the OUTERMOST call convert: a recursive
#: solution calls its own (now wrapped) name with real nodes.
_HELPERS: Final = """
_aca_depth = 0
_ACA_MAX_NODES = 100000


def _aca_tree(values):
    if not isinstance(values, list):
        return values
    if not values or values[0] is None:
        return None
    nodes = [None if value is None else TreeNode(value) for value in values]
    queue = [nodes[0]]
    index = 1
    while queue and index < len(nodes):
        node = queue.pop(0)
        node.left = nodes[index]
        index += 1
        if node.left is not None:
            queue.append(node.left)
        if index < len(nodes):
            node.right = nodes[index]
            index += 1
            if node.right is not None:
                queue.append(node.right)
    return nodes[0]


def _aca_list(values):
    if not isinstance(values, list):
        return values
    head = None
    for value in reversed(values):
        head = ListNode(value, head)
    return head


def _aca_plain(value, empty=None):
    if value is None:
        return empty
    if hasattr(value, "val") and hasattr(value, "left") and hasattr(value, "right"):
        out = []
        queue = [value]
        while queue and len(out) < _ACA_MAX_NODES:
            node = queue.pop(0)
            if node is None:
                out.append(None)
                continue
            out.append(node.val)
            queue.append(node.left)
            queue.append(node.right)
        while out and out[-1] is None:
            out.pop()
        return out
    if hasattr(value, "val") and hasattr(value, "next"):
        out = []
        while value is not None and len(out) < _ACA_MAX_NODES:
            out.append(value.val)
            value = value.next
        return out
    return value
"""


def _annotation_kind(annotation: ast.expr | None) -> NodeKind | None:
    if annotation is None:
        return None
    text = ast.unparse(annotation)
    if "TreeNode" in text:
        return "tree"
    if "ListNode" in text:
        return "list"
    return None


def _attrs_read_on(func: ast.AST, name: str) -> set[str]:
    return {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
    }


def _param_kind(func: ast.FunctionDef, arg: ast.arg, module_attrs: set[str]) -> NodeKind | None:
    """Is this parameter a tree, a linked list, or neither?

    The annotation decides when there is one. Otherwise what the function
    reads on the name (`root.left`, `head.next`). Otherwise -- a `root` that
    is only handed to an inner helper -- the conventional name, but only in a
    module that touches the matching attributes somewhere.
    """
    annotated = _annotation_kind(arg.annotation)
    if annotated is not None:
        return annotated
    if arg.annotation is not None:
        return None  # annotated as something else (`List[int]`): believe it
    read = _attrs_read_on(func, arg.arg)
    if read & _TREE_ATTRS:
        return "tree"
    if read & _LIST_ATTRS:
        return "list"
    if arg.arg in _TREE_NAMES and module_attrs & _TREE_ATTRS:
        return "tree"
    if arg.arg in _LIST_NAMES and module_attrs & _LIST_ATTRS:
        return "list"
    return None


def _wrapper(func: ast.FunctionDef, kinds: dict[str, NodeKind]) -> str | None:
    args = func.args
    if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
        return None
    names = [a.arg for a in args.args]
    defaults = [ast.unparse(d) for d in args.defaults]
    first_default = len(names) - len(defaults)
    params = [
        name if i < first_default else f"{name}={defaults[i - first_default]}"
        for i, name in enumerate(names)
    ]
    call = [
        f"_aca_{kinds[name]}({name})" if name in kinds else name  # tree / list converter
        for name in names
    ]
    returns = _annotation_kind(func.returns)
    empty = "[]" if returns is not None else "None"
    original = f"_aca_orig_{func.name}"
    return (
        f"{original} = {func.name}\n\n\n"
        f"def {func.name}({', '.join(params)}):\n"
        f"    global _aca_depth\n"
        f"    if _aca_depth:\n"
        f"        return {original}({', '.join(names)})\n"
        f"    _aca_depth += 1\n"
        f"    try:\n"
        f"        result = {original}({', '.join(call)})\n"
        f"    finally:\n"
        f"        _aca_depth -= 1\n"
        f"    return _aca_plain(result, {empty})\n"
    )


def adapt_nodes(code: str) -> tuple[str, int]:
    """`code` with its node-taking top-level functions callable with plain
    lists, and the number of lines put ABOVE the submission (0 or the length
    of the class definitions it needed), so reported line numbers can be
    mapped back. `(code, 0)` when there is nothing to adapt. Never raises."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return code, 0
    if "_aca_depth" in code:
        return code, 0  # already adapted

    module_attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    wrappers: list[str] = []
    used: set[NodeKind] = set()
    seen: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        if node.name in seen:
            continue
        seen.add(node.name)
        kinds: dict[str, NodeKind] = {}
        for arg in node.args.args:
            kind = _param_kind(node, arg, module_attrs)
            if kind is not None:
                kinds[arg.arg] = kind
        returns = _annotation_kind(node.returns)
        if not kinds and returns is None:
            continue
        wrapper = _wrapper(node, kinds)
        if wrapper is None:
            continue
        wrappers.append(wrapper)
        used.update(kinds.values())
        if returns is not None:
            used.add(returns)
    if not wrappers:
        return code, 0

    defined = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    names_used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    prelude: list[str] = []
    # A class the submission NAMES (in an annotation or a constructor call) but
    # does not define must exist before its `def` lines are evaluated, so it
    # goes above. One only the converters need can go below with them.
    below: list[str] = []
    for kind, name, source in (
        ("tree", "TreeNode", _TREE_CLASS),
        ("list", "ListNode", _LIST_CLASS),
    ):
        if name in defined:
            continue
        if name in names_used and "__future__" not in code:
            prelude.append(source)
        elif kind in used or name in names_used:
            below.append(source)
    head = "\n\n".join(prelude)
    shift = head.count("\n") + 2 if prelude else 0
    parts = [code.rstrip("\n"), *below, _HELPERS.strip("\n"), *wrappers]
    body = "\n\n\n".join(part.strip("\n") for part in parts) + "\n"
    if prelude:
        return f"{head}\n\n{body}", shift
    return body, 0
