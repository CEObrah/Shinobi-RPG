from pathlib import Path

path=Path(__file__).resolve().parents[1]/'tools/_ooc_improve_withdrawal_timeline.py'
text=path.read_text(encoding='utf-8')
needle="if 'test_withdrawal_completion_cancels_late_uncommitted_chase_on_shared_clock' not in test:\n"
replacement="addition=addition.replace('\\\\n','\\n')\n"+needle
if replacement not in text:
    if text.count(needle)!=1:
        raise SystemExit(f'patch needle count {text.count(needle)}')
    text=text.replace(needle,replacement,1)
path.write_text(text,encoding='utf-8')
print('repaired generated test newline handling')
