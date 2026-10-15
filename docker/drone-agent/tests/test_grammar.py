"""tests/test_grammar.py — тесты динамической грамматики GBNF."""
import pytest

from swarm.grammar import choice_grammar


def test_choice_grammar_valid():
    g1 = choice_grammar(1)
    assert '"0" | "1"' in g1

    g5 = choice_grammar(5)
    for i in range(6):
        assert f'"{i}"' in g5
    assert '"6"' not in g5


def test_choice_grammar_out_of_bounds():
    with pytest.raises(ValueError):
        choice_grammar(0)

    with pytest.raises(ValueError):
        choice_grammar(10)
