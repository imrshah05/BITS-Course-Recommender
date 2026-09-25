"""Build a reproducible JSON dataset while retaining every source handout."""

import argparse
import json
import os
from pathlib import Path
import tempfile

from preprocessing.handout_batch import process_handouts
from preprocessing.handout_record import build_handout_record


def dataset_from_batch(batch):
    """Build one record per extracted document, without merging course codes."""
    records = []
    failures = [dict(failure, stage='pdf_extraction') for failure in batch['failures']]
    for document in sorted(batch['documents'], key=lambda d: d['source_file']):
        try:
            record = build_handout_record(document['pages'])
            if record['source']['source_file'] is None:
                record['source']['source_file'] = document['source_file']
            elif record['source']['source_file'] != document['source_file']:
                raise ValueError('Document filename does not match extracted page source')
            records.append(record)
        except Exception as exc:
            failures.append({'source_file': document['source_file'], 'stage': 'normalization',
                             'error': f'{type(exc).__name__}: {exc}'})
    return {
        'records': records,
        'failures': sorted(failures, key=lambda f: (f['source_file'], f['stage'], f['error'])),
    }


def write_dataset(dataset, output_path):
    """Write stable UTF-8 JSON atomically; keep an existing output on failure."""
    output = Path(output_path)
    payload = json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def build_course_dataset(handout_directory, output_path):
    """Discover PDFs through the batch pipeline and persist their validated records."""
    folder = Path(handout_directory).resolve()
    output = Path(output_path).resolve()
    if output == folder or folder in output.parents or output.suffix.lower() != '.json':
        raise ValueError('Output must be a JSON file outside the source handout directory')
    dataset = dataset_from_batch(process_handouts(folder))
    write_dataset(dataset, output)
    return dataset


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handouts', default='data/raw/handouts')
    parser.add_argument('--output', default='data/processed/courses.json')
    args = parser.parse_args()
    result = build_course_dataset(args.handouts, args.output)
    print(f"Wrote {len(result['records'])} records; {len(result['failures'])} failures to {args.output}")
