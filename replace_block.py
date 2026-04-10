
path = '/var/www/mrktahsilat/templates/tahsilat/cek_senetler.html'

with open(path, 'r') as f:
    lines = f.readlines()

new_block_str = """                            <label class="form-label">Vade Durumu</label>
                            <select name="vade_durumu" class="form-select">
                                <option value="">Tümü</option>
                                <option value="vadesi_gecti" {% if filters.vade_durumu == 'vadesi_gecti' %}selected{% endif %}>Vadesi Geçenler</option>
                                <option value="vade_bugun" {% if filters.vade_durumu == 'vade_bugun' %}selected{% endif %}>Vade Bugün</option>
                                <option value="vade_yarin" {% if filters.vade_durumu == 'vade_yarin' %}selected{% endif %}>Vadesi Yarın</option>
                                <option value="bu_hafta" {% if filters.vade_durumu == 'bu_hafta' %}selected{% endif %}>Bu Hafta</option>
                                <option value="bu_ay" {% if filters.vade_durumu == 'bu_ay' %}selected{% endif %}>Bu Ay</option>
                                <option value="gelecek_ay" {% if filters.vade_durumu == 'gelecek_ay' %}selected{% endif %}>Gelecek Ay</option>
                            </select>"""

new_lines_to_insert = [line + '\n' for line in new_block_str.split('\n')]

start_idx = 1240 # Line 1241
end_idx = 1254   # Line 1255

if "Vade Durumu" in lines[start_idx] and "</select>" in lines[end_idx]:
    lines[start_idx:end_idx+1] = new_lines_to_insert
    
    with open(path, 'w') as f:
        f.writelines(lines)
    print("Successfully replaced Vade Durumu block.")
else:
    print("Context mismatch. Aborting.")
    print(f"Expected start 'Vade Durumu', found: {lines[start_idx].strip()}")
    print(f"Expected end '</select>', found: {lines[end_idx].strip()}")
