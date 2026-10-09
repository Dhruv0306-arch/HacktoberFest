"""Convert actual Markdown content into readable UI text without losing its words."""
from html import unescape
from html.parser import HTMLParser
import re
from markdown_it import MarkdownIt

PARSER = MarkdownIt('commonmark').enable('table')  # Already supplied by Gradio.


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def _html_text(value):
    parser = _Text()
    parser.feed(value)
    return ''.join(parser.parts)


def inline_text(tokens):
    output = []
    links = []
    for token in tokens or []:
        if token.type in {'text', 'code_inline'}:
            output.append(token.content)
        elif token.type in {'softbreak', 'hardbreak'}:
            output.append('\n')
        elif token.type == 'link_open':
            links.append(token.attrGet('href') or '')
        elif token.type == 'link_close':
            url = links.pop() if links else ''
            if url and (not output or url != output[-1]):
                output.append(' (' + url + ')')
        elif token.type == 'image':
            output.append(token.content)
            url = token.attrGet('src')
            if url:
                output.append(' (' + url + ')')
        elif token.type == 'html_inline':
            output.append(_html_text(token.content))
    return unescape(''.join(output))


def plain_markdown(value):
    if not isinstance(value, str):
        raise ValueError('Display content must be text.')
    lines = []
    for token in PARSER.parse(value):
        if token.type == 'inline':
            line = inline_text(token.children)
            line = re.sub(r'^\[ \]\s*', '☐ ', line)
            line = re.sub(r'^\[[xX]\]\s*', '☑ ', line)
            lines.append(line)
        elif token.type in {'fence', 'code_block'}:
            lines.append(token.content.rstrip('\n'))
        elif token.type == 'html_block':
            lines.append(_html_text(token.content))
    return '\n'.join(lines).strip()


def markdown_checklist(value):
    """Adapt a real Markdown checklist; retain non-task sections in its summary."""
    tokens = PARSER.parse(value)
    steps, notes = [], []
    section = ''
    item_depth = 0
    pieces = []
    task_section = True
    next_heading = False
    for token in tokens:
        if token.type == 'heading_open':
            next_heading = True
        elif token.type == 'inline':
            text = inline_text(token.children)
            if next_heading:
                section = text.lower()
                task_section = not any(word in section for word in ('evidence', 'missing', 'dates', 'links', 'source', 'contacts'))
                notes.append(text)
                next_heading = False
            elif item_depth:
                pieces.append(text)
            else:
                notes.append(text)
        elif token.type == 'list_item_open':
            item_depth += 1
        elif token.type == 'list_item_close':
            item_depth -= 1
            if item_depth == 0 and pieces:
                text = '\n'.join(pieces)
                pieces = []
                if task_section:
                    explicit_optional = 'optional' in section or bool(re.search(r'\boptional\b', text, re.I))
                    explicit_required = 'required' in section or bool(re.search(r'\brequired\b', text, re.I))
                    text = re.sub(r'^\[[ xX]\]\s*', '', text)
                    steps.append({'order': len(steps)+1, 'step': text, 'detail': '',
                                  'required': not explicit_optional, 'source_ref': '',
                                  '_requirement_known': explicit_optional or explicit_required})
                else:
                    notes.append(text)
        elif token.type in {'fence', 'code_block'}:
            (pieces if item_depth else notes).append(token.content.rstrip('\n'))
    return {'summary': '\n'.join(notes).strip(), 'steps': steps}
