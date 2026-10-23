"""Bounded exact source spans and lexical coverage hints, never factual proof."""
from __future__ import annotations

import re

# Instruction words carry no subject coverage requirement.
_STOP = frozenset('html markdown json url urls uri uris utc pdf csv xml utf api evidenceitem evidenceid evidenceids find produce create make build write generate investigate summarize analyse analyze compare comparison explain describe research report evidence sources source public official documentation document docs python please using use based task tasks technical overview introduction async await exception exceptions method methods function functions property properties and the with for from into about this that all only not are can how what which their these those between summary differences behaviour behavior examples include including'.split())


def objective_terms(objective: str) -> tuple[str, ...]:
    plain = re.sub(r'https?://\S+', ' ', objective)
    words = [w.casefold() for w in re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}', plain) if w.casefold() not in _STOP]
    return tuple(dict.fromkeys([*named_concepts(objective), *words]))[:32]


def covered_terms(text: str, terms: tuple[str, ...]) -> set[str]:
    return {t for t in terms if re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', text, re.I)}


def _units(text: str, limit: int) -> list[tuple[int, int, int]]:
    """Keep paragraphs intact; split oversized paragraphs at sentence boundaries.

    A single oversized sentence/code block is omitted rather than turned into a
    misleading incomplete assertion. Other complete sentences remain eligible.
    """
    units = []
    boundaries = [0, *(m.end() for m in re.finditer(r'\n[ \t]*\n', text))]
    for index, left in enumerate(boundaries):
        right = boundaries[index + 1] if index + 1 < len(boundaries) else len(text)
        raw = text[left:right]
        start = left + len(raw) - len(raw.lstrip())
        end = right - len(raw) + len(raw.rstrip())
        if start >= end:
            continue
        if end - start <= limit:
            units.append((start, end, index))
            continue
        offset = start
        for sentence in re.split(r'(?<=[.!?。！？])\s+', text[start:end]):
            position = text.find(sentence, offset, end)
            sentence_end = position + len(sentence)
            if sentence_end - position <= limit:
                units.append((position, sentence_end, index))
            offset = sentence_end

    return units


def evidence_spans(text: str, objective: str, *, limit: int = 1200,
                   max_spans: int = 6, max_bytes: int = 7200) -> list[tuple[int, int, int]]:
    """Select bounded complete passages, including shared introductory caveats.

    Offsets index the original Unicode string. Paragraph/definition boundaries
    precede lexical ranking; coverage and restriction words are only heuristics.
    No truncation is performed to fill a remaining character or UTF-8 byte cap.
    """
    if min(limit, max_spans, max_bytes) <= 0:
        raise ValueError('positive excerpt limits required')
    units = _units(text, limit)
    if not units:
        return []
    named = named_concepts(objective)
    terms = named or objective_terms(objective)
    restrictions = re.compile(
        r'\b(?:if|when|otherwise|other than|instead|not|only|unless|except|must|cannot|caveats?|limitations?|restrictions?)\b|不得|仅|除非|限制', re.I)

    def definition(value: str) -> bool:
        # Plain extracted documentation, Markdown headings and API signatures.
        return len(value) <= 160 and bool(re.search(r'¶$|^#{1,6} |^class |^async [A-Za-z_]\w*\(', value))

    # Lower-case API names in the objective are subjects too when the source
    # actually defines them; ordinary instruction prose is not a coverage goal.
    defined = set()
    for a, b, _ in units:
        value = text[a:b]
        if definition(value):
            signature = re.search(r'(?:[\w]+\.)*([A-Za-z][A-Za-z0-9_]*)\(', value)
            if signature:
                defined.add(signature[1].casefold())
    named = tuple(dict.fromkeys((*named, *(t for t in objective_terms(objective) if t in defined))))
    terms = named or terms
    scopes: dict[int, bool] = {}
    in_scope = False
    for a, b, _ in units:
        value = text[a:b]
        if definition(value):
            owner = re.match(r'class (?:[\w]+\.)*([A-Za-z][A-Za-z0-9_]*)', value)
            if owner:
                in_scope = owner[1].casefold() in named
            elif value.endswith('¶') and '(' not in value and '.' not in value:
                in_scope = bool(covered_terms(value, named))
            elif covered_terms(value, named):
                in_scope = True
        scopes[a] = in_scope
    has_scoped_content = any(scopes.values())
    candidates = []
    for i, (start, end, index) in enumerate(units):
        for next_start, next_end, _ in units[i + 1:]:
            # Do not bridge omitted oversized sentences or unrelated sections.
            if text[end:next_start].strip() or next_end - start > limit:
                break
            if (definition(text[next_start:next_end]) and scopes[start] and not scopes[next_start]):
                break
            if (definition(text[next_start:next_end])
                    and covered_terms(text[next_start:next_end], named)
                    and not (definition(text[start:units[i][1]])
                             and covered_terms(text[next_start:next_end], named)
                             <= covered_terms(text[start:units[i][1]], named))):
                break
            end = next_end
        candidates.append((start, end, index) if terms else (start, units[i][1], index))

    chosen: list[tuple[int, int, int]] = []
    seen: set[str] = set()
    covered: set[str] = set()
    remaining = max_bytes
    # Shared preamble before the first requested definition can qualify every
    # child object even when it never repeats their names (e.g. thread safety).
    first_definition = next((a for a, b, _ in units
                             if definition(text[a:b]) and covered_terms(text[a:b], named)), None)
    context = next((span for span in candidates if span[0] == units[0][0]), None)
    if (context is not None and first_definition is not None
            and 0 < first_definition <= limit * 2 and context[1] <= first_definition
            and restrictions.search(text[context[0]:context[1]])):
        candidates.remove(context)
        candidates.insert(0, context)
    else:
        context = None
    shared_context = context
    while candidates and len(chosen) < max_spans:
        def score(span: tuple[int, int, int]) -> tuple[int, int, int, int, int, int]:
            start, end, _ = span
            fragment = text[start:end]
            hits = covered_terms(fragment, terms)
            anchor = next((text[a:b] for a, b, _ in units if a == start), '')
            return (len((covered_terms(anchor, named) - covered)) if definition(anchor) else 0,
                    len((hits - covered) & set(named)), int(scopes[start]),
                    len(restrictions.findall(fragment)) if hits or scopes[start] else 0,
                    sum(len(re.findall(r'(?<!\w)' + re.escape(t) + r'(?!\w)', fragment, re.I)) for t in terms), -start)
        best = context if context in candidates else max(candidates, key=score)
        context = None
        candidates.remove(best)
        start, end, index = best
        if any(start < b and end > a for a, b, _ in chosen):
            # Retain nonoverlapping complete units at the edges of a passage.
            for a, b, paragraph in units:
                if start <= a and b <= end and not any(a < y and b > x for x, y, _ in chosen):
                    candidate = (a, b, paragraph)
                    if candidate not in candidates:
                        candidates.append(candidate)
            continue
        fragment = text[start:end]
        if (best != shared_context and has_scoped_content and not scopes[start]
                and covered_terms(fragment, terms) <= covered):
            continue
        if terms and not covered_terms(fragment, terms) and not scopes[start] and chosen:
            continue
        if (terms and len(fragment) < 40 and len(text.strip()) >= 40
                and not re.search(r'[.!?。！？]$', fragment)):
            continue
        size = len(fragment.encode('utf-8'))
        normalized = re.sub(r'\s+', ' ', fragment).strip().casefold()
        if not normalized or normalized in seen:
            continue
        if size > remaining:
            # Retry complete smaller units, never a partial sentence.
            for a, b, paragraph in units:
                if start <= a and b <= end and (a, b) != (start, end):
                    candidate = (a, b, paragraph)
                    if candidate not in candidates:
                        candidates.append(candidate)
            continue
        seen.add(normalized)
        chosen.append((start, end, index))
        remaining -= size
        if best != shared_context:
            covered |= covered_terms(fragment, terms)
    return sorted(chosen)


def evidence_span(text: str, objective: str, *, limit: int = 1000) -> tuple[int, int, int] | None:
    spans = evidence_spans(text, objective, limit=limit, max_spans=1, max_bytes=limit * 4)
    return spans[0] if spans else None


def named_concepts(objective: str) -> tuple[str, ...]:
    """Named/code concepts are coverage hints; prose words are not requirements."""
    plain = re.sub(r'https?://\S+', ' ', objective)
    named = re.finditer(r'`([A-Za-z][A-Za-z0-9_]*)`|(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9_]{2,})(?![A-Za-z0-9_])', plain)
    required = []
    for match in named:
        explicit, word = match.groups()
        token = explicit or word
        # A normal sentence-initial capital is not a code/name declaration.
        # Explicit code, acronyms and mixed-case identifiers still count there.
        prefix = plain[:match.start()].rstrip()
        sentence_start = not prefix or prefix.endswith(('.', '!', '?', ':', ';', '。', '！', '？', '：', '；')) or plain[:match.start()].endswith('\n')
        if (not explicit and sentence_start
                and re.fullmatch(r'[A-Z][a-z]+', token)
                and plain[match.end():].startswith(' ')):
            continue
        if token.casefold() not in _STOP:
            required.append(token.casefold())
    required.extend(word.casefold() for word in re.findall(r'\b(?:gather|create_task|wait_for|as_completed)\b', plain, re.I))
    concepts = tuple(dict.fromkeys(required))[:32]
    return concepts


def missing_concepts(objective: str, excerpts: list[str]) -> list[str]:
    concepts = named_concepts(objective)
    present = covered_terms('\n'.join(excerpts), concepts)
    return [c for c in concepts if c not in present]
