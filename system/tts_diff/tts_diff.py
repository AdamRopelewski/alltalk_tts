import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Explicit forms prevent words with unrelated meanings from becoming numbers.
FORMS = {
    0: 'zero zera zeru zerem zerze', 1: 'jeden jedna jedno jednego jednej jednemu jednym jedną jedne jedni',
    2: 'dwa dwie dwóch dwu dwom dwoma dwiema', 3: 'trzy trzech trzem trzema',
    4: 'cztery czterech czterem czterema', 5: 'pięć pięciu pięcioma',
    6: 'sześć sześciu sześcioma', 7: 'siedem siedmiu siedmioma',
    8: 'osiem ośmiu ośmioma', 9: 'dziewięć dziewięciu dziewięcioma',
    10: 'dziesięć dziesięciu dziesięcioma', 11: 'jedenaście jedenastu jedenastoma',
    12: 'dwanaście dwunastu dwunastoma', 13: 'trzynaście trzynastu trzynastoma',
    14: 'czternaście czternastu czternastoma', 15: 'piętnaście piętnastu piętnastoma',
    16: 'szesnaście szesnastu szesnastoma', 17: 'siedemnaście siedemnastu siedemnastoma',
    18: 'osiemnaście osiemnastu osiemnastoma', 19: 'dziewiętnaście dziewiętnastu dziewiętnastoma',
    20: 'dwadzieścia dwudziestu dwudziestoma', 30: 'trzydzieści trzydziestu trzydziestoma',
    40: 'czterdzieści czterdziestu czterdziestoma', 50: 'pięćdziesiąt pięćdziesięciu pięćdziesięcioma',
    60: 'sześćdziesiąt sześćdziesięciu sześćdziesięcioma', 70: 'siedemdziesiąt siedemdziesięciu siedemdziesięcioma',
    80: 'osiemdziesiąt osiemdziesięciu osiemdziesięcioma', 90: 'dziewięćdziesiąt dziewięćdziesięciu dziewięćdziesięcioma',
    100: 'sto stu stoma',
}
VALUES = {word: value for value, forms in FORMS.items() for word in forms.split()}
# Numeric expressions are kept whole before punctuation is discarded.
TOKEN = re.compile(r'(?<!\w)[+-]?\d+(?:[.,/:\-]\d+)+|(?<!\w)[+-]\d+|(?<!\w)\d+\.(?=\s+\w)|\w+', re.UNICODE)


def tokens(text, polish=False):
    matches = list(TOKEN.finditer(text))
    original = [m.group() for m in matches]
    raw = [word.casefold() for word in original]
    out = []
    i = 0
    while i < len(raw):
        word = raw[i]
        value = None
        count = 1
        if polish:
            if word.isdecimal() and (word == '0' or not word.startswith('0')):
                value = int(word)
            elif word in VALUES:
                value = VALUES[word]
                if value in range(20, 100, 10) and i + 1 < len(raw) and VALUES.get(raw[i + 1], 0) in range(1, 10) and text[matches[i].end():matches[i + 1].start()].isspace():
                    value += VALUES[raw[i + 1]]
                    count = 2
        key = ('number', value) if value is not None else ('text', word)
        out.append((key, original[i:i + count]))
        i += count
    return out


def normalize_text(text):
    return ' '.join(key[1] for key, _ in tokens(text))


def compare(original, whisper, threshold=98, polish=False):
    left, right = tokens(original, polish), tokens(whisper, polish)
    a, b = [x[0] for x in left], [x[0] for x in right]
    raw_a, raw_b = tokens(original), tokens(whisper)
    clean_a = ' '.join(x[0][1] for x in raw_a)
    clean_b = ' '.join(x[0][1] for x in raw_b)
    try:
        from fuzzywuzzy import fuzz
        similarity = fuzz.ratio(clean_a, clean_b)
    except ImportError:
        similarity = round(100 * SequenceMatcher(None, clean_a, clean_b).ratio())
    diff = []
    numeric_mismatch = False
    for tag, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal' and any(key[0] == 'number' or any(c.isdigit() for c in str(key[1])) for key in a[i:j] + b[k:l]):
            numeric_mismatch = True
        if tag == 'equal':
            for x, y in zip(left[i:j], right[k:l]):
                kind = 'equal'
                if x[0][0] == 'number' and [s.casefold() for s in x[1]] != [s.casefold() for s in y[1]]:
                    if any(s.isdecimal() for s in x[1]) != any(s.isdecimal() for s in y[1]):
                        kind = 'number_format'
                    else:
                        kind = 'replace'
                        numeric_mismatch = True
                diff.append({'type': kind, 'original': x[1], 'whisper': y[1]})
        else:
            diff.append({'type': tag, 'original': [s for _, words in left[i:j] for s in words], 'whisper': [s for _, words in right[k:l] for s in words]})
    exact = a == b and not numeric_mismatch
    has_number_format = any(part['type'] == 'number_format' for part in diff)
    number_format = exact and has_number_format
    # A numeric value mismatch always requires review, regardless of fuzzy score.
    spoken_punctuation = any(word in clean_b and word not in clean_a for word in ('dot', 'comma'))
    status = 'number_format' if number_format and not spoken_punctuation else 'ok' if (exact or (similarity > threshold and not numeric_mismatch and not has_number_format)) and not spoken_punctuation else 'review'
    return {'original': original, 'whisper': whisper, 'similarity': similarity, 'status': status, 'diff': diff}


def run(items, model, directory, threshold):
    total = len(items)
    for index, item in enumerate(items, 1):
        print(f'[AllTalk TTSDiff] Processing ID: {item["id"]}', file=sys.stderr, flush=True)
        try:
            name = unquote(urlsplit(item['fileUrl']).path).rsplit('/', 1)[-1]
            if not name or name in ('.', '..') or '/' in name or '\\' in name:
                raise ValueError('Invalid audio file reference')
            path = (directory / name).resolve()
            if path.parent != directory.resolve() or not path.is_file():
                raise ValueError('Audio file not found')
            text = model.transcribe(str(path))['text']
            result = compare(item['text'], text, threshold, item.get('language', '').lower() in ('pl', 'pl-pl', 'polish'))
        except Exception as exc:
            result = {'status': 'error', 'error': str(exc)}
        yield {'type': 'result', 'id': item['id'], 'completed': index, 'total': total, **result}
    yield {'type': 'complete', 'completed': total, 'total': total}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=int, default=98)
    parser.add_argument('--ttslistpath')
    parser.add_argument('--wavfilespath', required=True)
    parser.add_argument('--whisper-model', default='large-v3')
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    items = json.load(sys.stdin) if args.stream else json.loads(Path(args.ttslistpath).read_text(encoding='utf-8'))
    # Libraries and model output belong on stderr; stdout is reserved for JSON lines.
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        import whisper
        model = whisper.load_model(args.whisper_model, device='cuda' if torch.cuda.is_available() else 'cpu')
        records = []
        for record in run(items, model, Path(args.wavfilespath), args.threshold):
            if not args.stream:
                records.append(record)
            if args.stream:
                print(json.dumps(record, ensure_ascii=False), file=sys.__stdout__, flush=True)
    if not args.stream:
        results = [r for r in records if r['type'] == 'result']
        summary = {'flagged_ids': [r['id'] for r in results if r['status'] in ('review', 'error')], 'results': results}
        (Path(args.wavfilespath) / 'analysis_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
