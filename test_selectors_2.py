from bs4 import BeautifulSoup
import re

with open('/tmp/whatsapp_drawer_debug.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

drawer = soup.select_one('div[data-testid="group-info-drawer"], [role="region"]')
if drawer:
    items = drawer.select('div[role="listitem"]')
    for i, item in enumerate(items):
        text = item.get_text(separator=' ', strip=True)[:50]
        # Check name_el
        name_el = item.select_one('span[title], span[dir="auto"]')
        print(f"Item {i}: name_el={'YES ('+name_el.text[:15]+')' if name_el else 'NO'} | Text: {text}")
