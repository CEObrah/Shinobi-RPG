from pathlib import Path
import runpy

path = Path('tools/ooc_dev_patch_combat_rally.py')
text = path.read_text(encoding='utf-8')
old = '''    '("poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref")),\\n    "disengage"',
    '("poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies")),\\n    "disengage"',
'''
new = '''    '"poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref")),\\n    "disengage"',
    '"poison_ref","qi_allocation_milli","exchange_count","duration_seconds","until_resolution","improvised_prop_fact_ref","rally_allies")),\\n    "disengage"',
'''
if text.count(old) != 1:
    raise RuntimeError(f'patch matcher repair expected once, found {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
