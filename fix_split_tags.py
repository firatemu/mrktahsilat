
path = '/var/www/mrktahsilat/templates/tahsilat/cek_senetler.html'

with open(path, 'r') as f:
    lines = f.readlines()

new_lines = []
skip_next = False

for i in range(len(lines)):
    if skip_next:
        skip_next = False
        continue
        
    line = lines[i]
    next_line = lines[i+1] if i+1 < len(lines) else ""
    
    # Check for split {% endif %}
    # Example: "...selected{%\n" and "    endif %}>..."
    
    if "selected{%" in line and "endif %}" in next_line:
        # Join lines
        # Remove newline from first line
        joined = line.rstrip() + next_line.lstrip()
        new_lines.append(joined)
        skip_next = True
        print(f"Fixed split tag at line {i+1}")
    elif "selected{%" in line and "endif" in next_line:
        # Generic case
        joined = line.rstrip() + " " + next_line.lstrip()
        new_lines.append(joined)
        skip_next = True
        print(f"Fixed split tag at line {i+1}")
    else:
        new_lines.append(line)

with open(path, 'w') as f:
    f.writelines(new_lines)

print("Split tags fixed.")
