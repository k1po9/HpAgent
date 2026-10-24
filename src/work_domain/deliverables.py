"""Delivery requirements reuse exact ResultRefs and registered check evidence."""
from uuid import UUID

FORMATS = {'markdown': ('file.format.markdown', '1'), 'docx': ('file.format.docx', '1')}


def validate_deliverables(value, criteria):
    if value is None:
        return
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'items'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['items'], list) or not 1 <= len(value['items']) <= 10):
        raise ValueError('invalid deliverables contract')
    ids, used = set(), set()
    by_id = {c['id']: c for c in criteria}
    for item in value['items']:
        if (not isinstance(item, dict) or set(item) != {
                'id', 'format', 'required', 'output_criterion_id', 'format_criterion_id'}
                or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 80
                or item['id'] in ids or not isinstance(item['format'], str)
                or item['format'] not in FORMATS or type(item['required']) is not bool):
            raise ValueError('invalid delivery target')
        ids.add(item['id'])
        for key, kind in [('output_criterion_id', 'output_exists'), ('format_criterion_id', 'check_passed')]:
            cid = item[key]
            if not isinstance(cid, str) or cid in used:
                raise ValueError('delivery criteria must be distinct')
            criterion = by_id.get(cid)
            if not criterion or criterion['evidence_types'] != [kind] or criterion['required'] != item['required']:
                raise ValueError('delivery target must bind matching existence and format criteria')
            used.add(cid)


def validate_delivery_evidence(uow, run, requirement, evidence):
    value = requirement.get('deliverables')
    if value is None:
        return
    assigned: set[str] = set()
    for target in value['items']:
        if not target['required']:
            continue
        refs = []
        for cid, kind in [(target['output_criterion_id'], 'output_exists'),
                          (target['format_criterion_id'], 'check_passed')]:
            subjects = set()
            for item in evidence:
                if item['criterion_id'] != cid or item['type'] != kind:
                    continue
                row = uow.execute(
                    'SELECT predicate FROM capability_acceptance_evidence WHERE account_id=%s '
                    'AND run_id=%s AND evidence_id=%s AND criterion_id=%s',
                    (run['account_id'], run['run_id'], UUID(item['ref']), cid),
                ).fetchone()
                if row:
                    p = row['predicate']
                    if kind == 'output_exists' or (p.get('verifier_key'), p.get('verifier_version')) == FORMATS[target['format']]:
                        subjects.add(p['subject_ref'])
            refs.append(subjects)
        common = refs[0] & refs[1]
        if len(common) != 1 or common & assigned:
            raise ValueError('delivery existence and format need the same distinct ResultRef')
        assigned.update(common)
