from bs4 import BeautifulSoup

with open('/tmp/whatsapp_drawer_debug.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

drawer = soup.select_one('div[data-testid="group-info-drawer"], [role="region"]')
if drawer:
    items = drawer.select('div[role="listitem"]')
    print(f"Listitems INSIDE drawer: {len(items)}")
    for i, item in enumerate(items):
        text = item.get_text(separator=' ', strip=True)[:50]
        print(f"Drawer Item {i}: {text}")
else:
    print("NO DRAWER FOUND!")
