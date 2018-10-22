from nltk import word_tokenize
from typing import Callable, List, Type
from enum import Enum
import search
import reuters
from inverted_index import InvertedIndex, Posting


class TokenType(Enum):
    TERM = "TERM"
    OR = "OR"
    AND = "AND"
    NOT = "NOT"
    LPAREN = "("
    RPAREN = ")"
    EOF = "##EOF##"


class Token:
    def __init__(self, value, type: TokenType):
        self.value = value
        self.type = type

    def __str__(self):
        return "({type}, {value})".format(type=self.type, value=self.value)


def lexer(tokens: List[str]):
    """
    Creates tuple with terms,type.
    :param tokens:List of tokens in the expression
    :return: List of Tokens tuple (value,type)
    """
    toks = []
    for t in tokens:
        if t == TokenType.OR.value:
            toks.append(Token(value=t, type=TokenType.OR))
        elif t == TokenType.AND.value:
            toks.append(Token(value=t, type=TokenType.AND))
        elif t == TokenType.NOT.value:
            toks.append(Token(value=t, type=TokenType.NOT))
        elif t == TokenType.LPAREN.value:
            toks.append(Token(value=t, type=TokenType.LPAREN))
        elif t == TokenType.RPAREN.value:
            toks.append(Token(value=t, type=TokenType.RPAREN))
        elif t == TokenType.EOF.value:
            toks.append(Token(value=t, type=TokenType.EOF))
        else:
            toks.append(Token(value=t, type=TokenType.TERM))
    return toks


class ParseTree:
    pass


class BinOp(ParseTree):
    def __init__(self, left_child, op: Token, right_child):
        self.left_child = left_child
        self.right_child = right_child
        self.op = op


class Term(ParseTree):
    def __init__(self, term: Token):
        self.term = term


class UnaryOp(ParseTree):
    def __init__(self, op: Token, child):
        self.op = op
        self.child = child


class Parser:
    def __init__(self, expression: str):
        # self._expression = expression
        self._tokens = word_tokenize(expression)
        self._tokens.append(TokenType.EOF.value)
        self._tokens = lexer(self._tokens)
        self._tokens_stream = iter(self._tokens)
        self._current_token = next(self._tokens_stream)

    def parse(self):
        """
        Creates the ParseTree from the initialized expression
        :return ParseTree: Abstract Syntax Tree of the expression for evaluation
        """
        return self._expression()

    def _ingest(self, type: TokenType):
        if self._current_token.type == type:
            self._current_token = next(self._tokens_stream)
        else:
            raise ExpressionParserException(
                "Invalid Syntax. Expected type: {}, received: {}".format(type, self._current_token.type))

    def _expression(self):
        node = self._conjunction()
        while self._current_token.type == TokenType.OR:
            op_tok = self._current_token
            self._ingest(TokenType.OR)
            node = BinOp(node, op_tok, self._conjunction())
        return node

    def _conjunction(self):
        node = self._term()
        while self._current_token.type == TokenType.AND:
            op_tok = self._current_token
            self._ingest(TokenType.AND)
            node = BinOp(node, op_tok, self._term())
        return node

    def _term(self):
        tok = self._current_token
        if tok.type == TokenType.NOT:
            self._ingest(TokenType.NOT)
            node = UnaryOp(tok, self._term())
        elif tok.type == TokenType.TERM:
            self._ingest(TokenType.TERM)
            node = Term(tok)
        elif tok.type == TokenType.LPAREN:
            self._ingest(TokenType.LPAREN)
            node = self._expression()
            self._ingest(TokenType.RPAREN)
        return node


class Evaluator:
    def __init__(self, parser: Parser, index: InvertedIndex):
        self._parser = parser
        self._index = index
        self._reset()

    def _reset(self):
        self.eval_result = EvaluationResult()

    def _visit(self, node):
        if isinstance(node, BinOp):
            return self._visit_binop(node)
        elif isinstance(node, Term):
            return self._visit_term(node)
        elif isinstance(node, UnaryOp):
            return self._visit_unaryop(node)
        else:
            raise ExpressionParserException("Invalid Node Type: Aborting!")

    def _visit_binop(self, node):
        if node.op.type == TokenType.AND:
            left_postings = self._visit(node.left_child)
            right_postings = self._visit(node.right_child)
            # Fix to disregard compression which removes terms (Stop Words, no numbers)
            # This is required for AND operation to not erroneously return no documents
            if left_postings is None and right_postings is None:
                return None
            elif left_postings is None:
                return right_postings
            elif right_postings is None:
                return left_postings
            else:
                return search.intersect(left_postings, right_postings)
        elif node.op.type == TokenType.OR:
            return search.union(self._visit(node.left_child), self._visit(node.right_child))
        else:
            raise ExpressionParserException("Invalid Binary Operator Type: Aborting!")

    def _visit_term(self, node):
        postings = self._index.get_postings(node.term.value)
        if postings is None:
            self.eval_result.add_postings(node.term.value, [])
        else:
            self.eval_result.add_postings(node.term.value, postings)
        return postings

    def _visit_unaryop(self, node):
        return search.neg(self._index.get_universe(), self._visit(node.child))

    def evaluate(self):
        self._reset()
        tree = self._parser.parse()
        query_result = self._visit(tree)
        query_result = query_result if query_result is not None else []
        self.eval_result.update_results(query_result)

        return self.eval_result


class EvaluationResult:
    def __init__(self):
        self.postings_map = {}
        self.term_map = {}
        self.query_result = None
        self.results = None
        self.complete = False

    def add_postings(self, term: str, postings: List[Posting]):
        self.postings_map[term] = postings
        for p in postings:
            if p.docid not in self.term_map:
                self.term_map[p.docid] = []
            self.term_map[p.docid].append(term)

    def update_results(self, query_result: List[Posting]):
        self.query_result = query_result
        self.results = {posting.docid: {
            "positions": posting.positions,
            "terms": self.term_map[posting.docid] if posting.docid in self.term_map else []
        } for posting in self.query_result}

        self.complete = True

    def get_postings(self, term: str):
        postings = None
        if term in self.postings_map:
            postings = self.postings_map[term]
        else:
            postings = []

    def get_terms(self, docid: int):
        terms = None
        if docid in self.term_map:
            terms = self.term_map[docid]

        return terms if terms is not None else []

    def update_details(self, reuters_path: str, docid: int = None):
        if docid:
            reuters_details = reuters.docs_details([docid], reuters_path)
        else:
            reuters_details = reuters.docs_details(self.results.keys(), reuters_path)

        for docid, doc in reuters_details.items():
            if docid in self.results:
                self.results[docid]["title"] = doc.get_title()
                self.results[docid]["doc"] = doc


class ExpressionParserException(Exception):
    pass
