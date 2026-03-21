from bs4 import BeautifulSoup

with open('/tmp/whatsapp_drawer_debug.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')
elements = soup.find_all(string=lambda text: text and "view all" in text.lower())
print(f"Found 'view all' text matches: {len(elements)}")
for el in elements:
    print(f"Parent tag: {el.parent.name}, Text: '{el.strip()}'")
