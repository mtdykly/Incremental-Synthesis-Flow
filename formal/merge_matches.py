"""Import only current, compatible, one-to-one local proofs."""
import copy
from formal.build_match_problem import design_digest, build_match_problem


def merge_matches(base, new, match, report):
    if report.get('design_digest') != design_digest(base, new):
        raise ValueError('formal results belong to different input designs')
    result = copy.deepcopy(match)
    mapping = {x['base']: x['new'] for x in match['correspondences']}
    for job in report['jobs']:
        b, n = job['base'], job['new']
        if (job['status'] != 'proven' or job.get('proof_kind') != 'local_cell'
                or b not in result['base_unmatched'] or n not in result['new_unmatched']):
            continue
        build_match_problem(base, new, b, n, mapping)
        result['matches'].append({'base': b, 'new': n, 'method': 'formal_local', 'reuse': True})
        result['base_unmatched'].remove(b)
        result['new_unmatched'].remove(n)
    return result
