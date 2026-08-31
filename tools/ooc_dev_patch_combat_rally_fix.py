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
text = text.replace(old, new, 1)
text += r'''

replace_once(
    "tests/current/test_combat_geometry.py",
    "    combat=initialize_combat(combat_ref='defense-clock',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]})\n",
    "    combat=initialize_combat(combat_ref='defense-clock',side_a_refs=[attacker['person_id']],side_b_refs=[defender['person_id']],people=people,zone_ref='site.house_tang',started_at='x',objective={'kind':'eliminate','target_refs':[defender['person_id']]})\n    combat['positions'][attacker['person_id']].update(x_mm=0,y_mm=0)\n    combat['positions'][defender['person_id']].update(x_mm=900,y_mm=0)\n",
)
'''
path.write_text(text, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
