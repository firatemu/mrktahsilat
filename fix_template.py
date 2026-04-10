import re
import sys

filename = '/var/www/mrktahsilat/templates/tahsilat/ortalama_maliyet.html'

try:
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    def fix_tag(match):
        # Replce all newlines and multiple spaces with a single space
        return re.sub(r'\s+', ' ', match.group(0))

    # Fix {% ... %} tags that are split
    content = re.sub(r'\{%[^%]*%\}', fix_tag, content)
    
    # Fix {{ ... }} tags that are split
    content = re.sub(r'\{\{[^}]*\}\}', fix_tag, content)

    # Some {% if %} blocks end up without a space if they were split like {% if ... %}  selected{% endif %}
    # We should also ensure spacing around == doesn't break, which we just did using multi_replace_file_content
    # The previous regex replace failed likely because the file wasn't written.
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print("SUCCESS: Tags fixed")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
