
path = '/var/www/mrktahsilat/templates/tahsilat/cek_senetler.html'

with open(path, 'r') as f:
    lines = f.readlines()

# Look for .filter-card { definition
target_line_idx = -1
for i, line in enumerate(lines):
    if '.filter-card {' in line:
        target_line_idx = i
        break

if target_line_idx != -1:
    # Check if previous lines contain closing brace
    # Just to be sure we are not inserting double braces if I misread
    prev_line = lines[target_line_idx-1].strip()
    if prev_line != '}':
        print(f"Inserting closing brace before line {target_line_idx+1}")
        lines.insert(target_line_idx, '    }\n\n')
        
        with open(path, 'w') as f:
            f.writelines(lines)
        print("Success.")
    else:
        print("Closing brace already seems to be there.")
else:
    print("Could not find .filter-card {")
