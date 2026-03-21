from bs4 import BeautifulSoup

with open('/tmp/whatsapp_drawer_debug.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

items = soup.select('div[role="listitem"]')
print(f"Total listitems found: {len(items)}")

for i, item in enumerate(items):
    text = item.get_text(separator=' ', strip=True)
    name_el = item.select_one('span[title], span[dir="auto"]')
    print(f"Item {i}: name_el={'YES ('+name_el.text[:15]+')' if name_el else 'NO'} | Text: {text[:50]}")
