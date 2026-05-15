import json
base = r'c:\Users\david\narrative-state-engine\framework-local\ab-test\qwen35-pipelined\catalogs'
chars = len(json.load(open(base+r'\characters.json'))['characters'])
locs = len(json.load(open(base+r'\locations.json'))['locations'])
items = len(json.load(open(base+r'\items.json'))['items'])
facs = len(json.load(open(base+r'\factions.json'))['factions'])
print(f'Chars: {chars}, Locs: {locs}, Items: {items}, Factions: {facs}, Total: {chars+locs+items+facs}')
