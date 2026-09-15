"""Execute candidate miters. Unknown/error results never grant reuse."""
import json
from pathlib import Path
from formal.build_match_problem import build_match_problem, design_digest
from yosys_runner import quote, run_yosys


def run_matching(base, new, match, directory, yosys='yosys', timeout=30):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mapping = {x['base']: x['new'] for x in match['correspondences']}
    report = {'schema_version': 1, 'design_digest': design_digest(base, new), 'jobs': []}
    for group in match['formal_candidates']:
        for candidate in group['candidates']:
            b, n = group['base'], candidate['new']
            job = {'base': b, 'new': n, 'status': 'unknown', 'proof_kind': 'local_cell'}
            index = len(report['jobs'])
            try:
                problem, identities = build_match_problem(base, new, b, n, mapping)
                data = directory / f'{index:04d}.json'
                data.write_text(json.dumps(problem))
                script = directory / f'{index:04d}.ys'
                script.write_text(f'read_json {quote(data)}\n'
                                  'miter -equiv -flatten gold gate miter\n'
                                  'hierarchy -top miter\ntechmap\nopt\n'
                                  'sat -prove trigger 0 -show-inputs -show-outputs miter\n')
                result = run_yosys(script, directory / f'{index:04d}.log', yosys, timeout)
                job.update(result, inputs=identities)
                log = Path(result['log']).read_text()
                if result['status'] == 'passed':
                    if 'SAT proof finished - no model found: SUCCESS!' in log:
                        job['status'] = 'proven'
                    elif 'SAT proof finished - model found: FAIL!' in log:
                        job['status'] = 'disproven'
                    else:
                        job['status'] = 'unknown'
            except ValueError as exc:
                job['reason'] = str(exc)
            report['jobs'].append(job)
            (directory / 'formal_results.json').write_text(json.dumps(report, indent=2))
    (directory / 'formal_results.json').write_text(json.dumps(report, indent=2))
    return report
