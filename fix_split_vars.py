
path = '/var/www/mrktahsilat/templates/tahsilat/cek_senetler.html'

with open(path, 'r') as f:
    lines = f.readlines()

# Target is around line 1339/1340
# We look for lines ending with '{{' and next line starting with content and '}}'

fixed_count = 0
i = 0
new_lines = []

while i < len(lines):
    line = lines[i]
    if i + 1 < len(lines):
        next_line = lines[i+1]
        
        # Check for {{ at end of line and }} in next line variable
        if line.strip().endswith('{{') and '}}' in next_line:
            # Check specifically for the reported issue to be safe, or generic fix
            if "badge-odeme-" in line or True: # Generic fix for split variables is generally good
                print(f"Fixing split variable at line {i+1}")
                # Join them
                # line has indentation and content + {{
                # next_line has indentation + variable + }} + ...
                
                # We want: ... {{ variable }} ...
                
                joined = line.rstrip() + " " + next_line.lstrip()
                new_lines.append(joined)
                i += 2 # Skip next line
                fixed_count += 1
                continue
    
    new_lines.append(line)
    i += 1

if fixed_count > 0:
    with open(path, 'w') as f:
        f.writelines(new_lines)
    print(f"Fixed {fixed_count} split variable tags.")
else:
    print("No split variable tags found.")
