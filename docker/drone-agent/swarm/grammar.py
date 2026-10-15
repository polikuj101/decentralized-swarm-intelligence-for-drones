"""swarm/grammar.py — GBNF на каждый вызов: модель выбирает ИНДЕКС из меню, не имя инструмента."""
_TEMPLATE = r'''root ::= "{" ws "\"choice\":" ws idx "," ws "\"reasoning\":" ws text ws "}"
idx  ::= @IDX@
text ::= "\"" [^"\\\x7F\x00-\x1F]{1,100} "\""
ws   ::= " "?
'''


def choice_grammar(n_options: int) -> str:
    """индексы 0..n_options (0 = NO_ACTION); n_options <= 9"""
    if not 1 <= n_options <= 9:
        raise ValueError("n_options must be 1..9")
    return _TEMPLATE.replace("@IDX@", " | ".join(f'"{i}"' for i in range(n_options + 1)))
