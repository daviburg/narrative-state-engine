import sys, os, json, time
sys.path.insert(0, 'tools')
print('1. Loading modules...', flush=True)
from llm_client import LLMClient
from semantic_extraction import extract_and_merge, load_template, format_discovery_prompt, format_known_entities_bounded
from catalog_merger import load_catalogs, load_events
from temporal_extraction import load_timeline
print('2. Creating LLM client...', flush=True)
llm = LLMClient('config/llm.json')
print(f'3. LLM client OK: {llm.model}', flush=True)
catalog_dir = 'framework-local/ab-test/b70-full-b/catalogs'
print('4. Loading catalogs...', flush=True)
catalogs = load_catalogs(catalog_dir)
events_list = load_events(catalog_dir)
timeline = load_timeline(catalog_dir)
entity_count = sum(len(v) for v in catalogs.values())
print(f'5. Loaded {entity_count} entities', flush=True)
turn = {'turn_id': 'turn-135', 'speaker': 'dm', 'text': 'The morning sun filters through the tavern windows as Kael finishes his breakfast.'}
print('6. Calling extract_and_merge for turn-135...', flush=True)
t0 = time.time()
catalogs, events_list, failed, log_rec = extract_and_merge(turn, catalogs, events_list, llm, 0.6, catalog_dir=catalog_dir, timeline=timeline)
elapsed = time.time() - t0
new_ents = log_rec.get('new_entities', 0)
print(f'7. DONE in {elapsed:.1f}s. Failed={failed}, new_entities={new_ents}', flush=True)
