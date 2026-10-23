from research_domain.excerpts import evidence_span


def test_excerpt_finds_relevant_late_source_bytes_and_keeps_exact_offsets():
    text = 'Unrelated introduction.\n\n' + ('Background material. ' * 200) + '\n\nTaskGroup waits for all tasks before leaving.\n\nMore detail.'
    span = evidence_span(text, 'Compare TaskGroup and Lock', limit=100)
    assert span is not None
    start, end, paragraph = span
    assert 'TaskGroup waits for all tasks' in text[start:end]
    assert end - start <= 100
    assert start > 1000
    assert paragraph >= 1


def test_excerpt_fallback_preserves_first_paragraph_and_empty_input():
    text = 'First paragraph.\n\nSecond paragraph.'
    assert evidence_span(text, '中文目标') == (0, 16, 0)
    assert evidence_span('  \n\n', 'TaskGroup') is None


def test_excerpt_scoring_handles_unicode_without_moving_source_offsets():
    text = 'ß' * 1500 + '\n\nLock acquisition is fair.\n\nEvent details.'
    span = evidence_span(text, 'Lock acquisition', limit=100)
    assert span is not None
    assert 'Lock acquisition is fair' in text[span[0]:span[1]]


def test_multiple_spans_cover_distributed_concepts_and_exact_unicode_offsets():
    from research_domain.excerpts import evidence_spans, missing_concepts
    text = 'ß 漢字\n\n' + '\n\n'.join(name + ' details. ' + 'x ' * 2000 for name in ('Nursery', 'Semaphore', 'Mailbox'))
    spans = evidence_spans(text, 'Compare Nursery Semaphore Mailbox', limit=300)
    fragments = [text[a:b] for a, b, _ in spans]
    assert not missing_concepts('Compare Nursery Semaphore Mailbox', fragments)
    assert missing_concepts('Compare Nursery AbsentPrimitive', fragments) == ['absentprimitive']
    assert all(0 <= a < b <= len(text) for a, b, _ in spans)
    assert all(not (a < d and b > c) for i, (a, b, _) in enumerate(spans) for c, d, _ in spans[i + 1:])


def test_excerpt_dedup_and_caps_include_utf8_bytes():
    from research_domain.excerpts import evidence_spans
    text = ('Navigation menu.\n\n' * 40) + ('漢字 ' * 1000)
    spans = evidence_spans(text, '', limit=300, max_spans=3, max_bytes=500)
    fragments = [text[a:b] for a, b, _ in spans]
    assert len(spans) <= 3
    assert sum(len(f.encode()) for f in fragments) <= 500
    assert fragments.count('Navigation menu.') == 1
    assert len(set(fragments)) == len(fragments)


def test_output_formats_and_citation_metadata_are_not_subject_concepts():
    from research_domain.excerpts import missing_concepts
    assert missing_concepts('Compare Nursery and Mailbox; deliver HTML with Markdown references and URL citations',
                            ['Nursery manages tasks. Mailbox carries messages.']) == []


def test_sentence_capitalization_is_not_a_missing_named_concept():
    from research_domain.excerpts import missing_concepts
    assert missing_concepts('Track facts', ['fixed evidence']) == []
    assert missing_concepts('Investigate facts', ['fixed evidence']) == []
    assert missing_concepts('MissingPrimitive behavior', ['fixed evidence']) == ['missingprimitive']
    assert missing_concepts('`Semaphore` behavior', ['fixed evidence']) == ['semaphore']


def test_later_sentence_instructions_do_not_create_evidence_requirements():
    from research_domain.excerpts import missing_concepts
    assert missing_concepts('Compare Nursery and Mailbox. Provide accurate references.\nEnsure coverage.',
                            ['Nursery manages tasks; Mailbox carries messages.']) == []


def test_named_concepts_take_priority_over_long_instruction_prose():
    from research_domain.excerpts import evidence_spans, missing_concepts
    prose = ' '.join('instructionword' + str(i) for i in range(40))
    objective = prose + ' Compare Nursery Mailbox Semaphore'
    text = '\n\n'.join(n + ' facts. ' + 'background ' * 500 for n in ('Nursery', 'Mailbox', 'Semaphore'))
    spans = evidence_spans(text, objective, limit=200)
    assert not missing_concepts(objective, [text[a:b] for a, b, _ in spans])


def test_passages_keep_sentences_and_do_not_spend_byte_remainder_on_fragments():
    from research_domain.excerpts import evidence_spans

    sentence = 'A StorageBox retains files unless the retention period expires.'
    text = ('Irrelevant background. ' * 40) + sentence + ' More background. ' * 40
    spans = evidence_spans(text, 'Describe StorageBox', limit=100, max_bytes=130)
    assert any(sentence in text[a:b] for a, b, _ in spans)
    assert all(text[a:b].endswith('.') for a, b, _ in spans)
    assert sum(len(text[a:b].encode()) for a, b, _ in spans) <= 130
    # An oversized indivisible rule is a gap, never a partial asserted fact.
    assert evidence_spans('StorageBox ' + 'x' * 500, 'StorageBox', limit=100) == []


def test_frozen_source_passages_keep_common_limits_fairness_and_full_gather_rules():
    import json
    from pathlib import Path

    from research_domain.excerpts import evidence_spans

    fixture = json.loads((Path(__file__).parent / 'fixtures/research/content_passages.json').read_text())
    selected = []
    for source in fixture['sources']:
        text = source['text']
        spans = evidence_spans(text, 'Compare TaskGroup gather Lock Event Queue')
        fragments = [text[a:b] for a, b, _ in spans]
        assert len(spans) <= 6
        assert sum(len(f.encode()) for f in fragments) <= 7200
        assert all(len(f) <= 1200 for f in fragments)
        if 'asyncio-sync' in source['canonical_uri']:
            assert all('Condition¶' not in fragment for fragment in fragments)
        selected.extend(fragments)
    result = '\n'.join(selected)
    for rule in (
        'Acquiring a lock is fair:',
        'be used for OS thread synchronization',
        'This method waits until the lock is unlocked, sets it to\nlocked and returns True.',
        'won’t be cancelled and will continue to run.',
        'call is not cancelled in this case.',
        'with an exception other than asyncio.CancelledError,\nthe remaining tasks in the group are cancelled.',
        'If maxsize is less than or equal to zero, the queue size is\ninfinite.',
    ):
        assert rule in result


def test_other_topic_keeps_shared_eligibility_and_unicode_offsets_under_caps():
    from research_domain.excerpts import evidence_spans

    text = ('入馆政策¶\n\nAll admission passes require advance booking. '
            'They must not be used on closure days.\n\n'
            'YouthPass¶\n\nYouthPass is available only to visitors under 18. '
            'A guardian must accompany visitors under 12.\n\n'
            'ResidentPass¶\n\nResidentPass requires local residency proof. '
            'Temporary addresses are not accepted.\n\n'
            'GiftShop¶\n\nUnrelated shop catalogue. ' + 'product ' * 300)
    spans = evidence_spans(text, 'Compare YouthPass ResidentPass', limit=220, max_spans=3, max_bytes=700)
    result = '\n'.join(text[a:b] for a, b, _ in spans)
    for rule in ('must not be used on closure days.', 'under 12.', 'not accepted.'):
        assert rule in result
    assert 'shop catalogue' not in result
    assert sum(len(text[a:b].encode()) for a, b, _ in spans) <= 700
    assert spans == evidence_spans(text, 'Compare YouthPass ResidentPass', limit=220, max_spans=3, max_bytes=700)
    for a, b, _ in spans:
        assert text[a:b].encode().decode() == text[a:b]
