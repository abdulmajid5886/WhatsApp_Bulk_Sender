from bs4 import BeautifulSoup
import re

with open('/tmp/whatsapp_drawer_debug.html', 'r', encoding='utf-8') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

# Search for any text containing "member"
for span in soup.find_all(['span', 'div']):
    text = span.get_text(strip=True)
    if "member" in text.lower():
        print(f"Tag: {span.name}, Text: '{text}', Classes: {span.get('class')}")
