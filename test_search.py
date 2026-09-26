import urllib.request, json

req = urllib.request.Request(
    'http://localhost:8000/api/search',
    data=json.dumps({'query': 'arduino uno', 'platforms': ['amazon']}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    for item in data.get('amazon', []):
        print(f"  {item['title'][:60]} — EUR {item['price']}")
    if not data.get('amazon'):
        print("Nessun risultato Amazon (PA-API non configurata o nessun prodotto)")
        print(json.dumps(data, indent=2))
except urllib.error.HTTPError as e:
    print('Errore HTTP:', e.read().decode())
